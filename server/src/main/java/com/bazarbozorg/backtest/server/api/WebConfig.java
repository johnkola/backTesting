package com.bazarbozorg.backtest.server.api;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * The client is a separate origin (static files behind nginx), so the browser
 * needs CORS headers on every answer and on the preflight. The same policy as
 * the Node API: one allowed origin from {@code CORS_ALLOW_ORIGIN} (default
 * any), no credentials.
 */
@Configuration
public class WebConfig implements WebMvcConfigurer {
    private final String allowOrigin;

    public WebConfig(@Value("${cors.allow-origin}") String allowOrigin) {
        this.allowOrigin = allowOrigin;
    }

    @Override
    public void addCorsMappings(CorsRegistry registry) {
        registry.addMapping("/**")
                .allowedOrigins(allowOrigin)
                .allowedMethods("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
                .allowedHeaders("Content-Type")
                .maxAge(600);
    }
}
