package com.bazarbozorg.backtest.strategy.nn;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.net.http.HttpClient;

import static org.junit.jupiter.api.Assertions.*;

/**
 * The strategy keeps its own HttpClient (tuned for 15-minute trains and the
 * 404 → ModelNotCachedException mapping), so it needs the same HTTP/1.1 pin as
 * {@code LoaderClient} — see that class's test for what breaks without it.
 * Both {@code train} and {@code run -s nn-feedforward} POST through here.
 */
class NeuralNetworkStrategyHttpTest {

    @Test
    @DisplayName("the strategy's loader client is pinned to HTTP/1.1")
    void clientIsPinnedToHttp11() {
        assertEquals(HttpClient.Version.HTTP_1_1,
                NeuralNetworkStrategy.httpClient().version());
    }
}
