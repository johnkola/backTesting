package com.bazarbozorg.backtest.loader;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.net.http.HttpClient;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Guards the HTTP/1.1 pin. On the JDK default (HTTP/2, attempting an h2c
 * upgrade) the request body is dropped against uvicorn, which speaks 1.1 only:
 * the loader receives zero bytes and rejects the call with a 422 naming the
 * whole body as missing. Every POST from Java to the loader breaks that way,
 * and no Python test catches it — those drive FastAPI through TestClient,
 * never over a socket.
 */
class LoaderClientTest {

    @Test
    @DisplayName("the shared loader client is pinned to HTTP/1.1")
    void clientIsPinnedToHttp11() {
        assertEquals(HttpClient.Version.HTTP_1_1, LoaderClient.httpClient().version());
    }

    @Test
    @DisplayName("query values are URL-encoded")
    void encodesQueryValues() {
        assertEquals("nn-feedforward", LoaderClient.encode("nn-feedforward"));
        assertEquals("a+b%2Fc", LoaderClient.encode("a b/c"));
    }
}
