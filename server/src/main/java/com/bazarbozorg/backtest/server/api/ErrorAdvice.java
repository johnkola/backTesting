package com.bazarbozorg.backtest.server.api;

import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.ResponseEntity;
import org.springframework.web.ErrorResponse;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

/**
 * One error shape for every route: {@code {"error": "..."}} with the status
 * that fits. The client reads exactly this from the Node API, so a page works
 * the same whichever backend answered.
 *
 * <p>A refusal the code raised on purpose ({@link ApiException}) and the
 * framework's own (no such route, wrong method, a parameter of the wrong
 * type) keep their status and say why. Anything else is a 500 that names the
 * exception: this is an operator's tool on a private network, and "internal
 * error" with the reason hidden in a log helps nobody.
 */
@RestControllerAdvice
public class ErrorAdvice {
    private static final Logger log = LoggerFactory.getLogger(ErrorAdvice.class);

    @ExceptionHandler(ApiException.class)
    public ResponseEntity<Map<String, Object>> refused(ApiException e) {
        return answer(e.status(), e.getMessage());
    }

    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    public ResponseEntity<Map<String, Object>> wrongType(MethodArgumentTypeMismatchException e) {
        return answer(HttpStatus.BAD_REQUEST,
                "'" + e.getValue() + "' is not a valid value for " + e.getName());
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<Map<String, Object>> anything(Exception e) {
        if (e instanceof ErrorResponse framework) {
            // No such route, method not allowed, unreadable body and the like.
            String detail = framework.getBody().getDetail();
            return answer(framework.getStatusCode(), detail != null ? detail : e.getMessage());
        }
        log.error("unhandled", e);
        return answer(HttpStatus.INTERNAL_SERVER_ERROR,
                e.getClass().getSimpleName() + ": " + e.getMessage());
    }

    private static ResponseEntity<Map<String, Object>> answer(HttpStatusCode status, String message) {
        return ResponseEntity.status(status).body(Map.of("error", String.valueOf(message)));
    }
}
