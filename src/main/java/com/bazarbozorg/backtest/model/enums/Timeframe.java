package com.bazarbozorg.backtest.model.enums;

import java.time.Duration;
import java.util.HashMap;
import java.util.Map;

public enum Timeframe {

    M1("1 Minute", Duration.ofMinutes(1)),
    M5("5 Minutes", Duration.ofMinutes(5)),
    M15("15 Minutes", Duration.ofMinutes(15)),
    M30("30 Minutes", Duration.ofMinutes(30)),
    H1("1 Hour", Duration.ofHours(1)),
    H4("4 Hours", Duration.ofHours(4)),
    D1("1 Day", Duration.ofDays(1)),
    W1("1 Week", Duration.ofDays(7)),
    MN("1 Month", Duration.ofDays(30));

    private final String displayName;
    private final Duration duration;

    private static final Map<String, Timeframe> CODE_MAP = new HashMap<>();

    static {
        for (Timeframe tf : values()) {
            CODE_MAP.put(tf.name(), tf);
            CODE_MAP.put(tf.name().toLowerCase(), tf);
            CODE_MAP.put(tf.displayName.toLowerCase(), tf);
        }
    }

    Timeframe(String displayName, Duration duration) {
        this.displayName = displayName;
        this.duration = duration;
    }

    public String getDisplayName() {
        return displayName;
    }

    public Duration getDuration() {
        return duration;
    }

    /**
     * Resolves a Timeframe from a string code such as "M1", "H4", "D1", etc.
     *
     * @param code the timeframe code (case-insensitive)
     * @return the matching Timeframe
     * @throws IllegalArgumentException if no match is found
     */
    public static Timeframe fromCode(String code) {
        if (code == null || code.isBlank()) {
            throw new IllegalArgumentException("Timeframe code must not be null or blank");
        }
        Timeframe tf = CODE_MAP.get(code.trim().toLowerCase());
        if (tf == null) {
            throw new IllegalArgumentException("Unknown timeframe code: " + code);
        }
        return tf;
    }
}
