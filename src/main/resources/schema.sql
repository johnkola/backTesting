CREATE TABLE IF NOT EXISTS instruments (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL UNIQUE,
    name VARCHAR(100),
    type VARCHAR(20) NOT NULL,
    price_precision INT DEFAULT 2,
    pip_size DOUBLE DEFAULT 0.01
);

CREATE TABLE IF NOT EXISTS candles (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    instrument_id BIGINT NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    open DOUBLE NOT NULL,
    high DOUBLE NOT NULL,
    low DOUBLE NOT NULL,
    close DOUBLE NOT NULL,
    volume DOUBLE DEFAULT 0,
    FOREIGN KEY (instrument_id) REFERENCES instruments(id),
    UNIQUE (instrument_id, timeframe, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_candles_lookup ON candles(instrument_id, timeframe, timestamp);

CREATE TABLE IF NOT EXISTS backtest_results (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    instrument_symbol VARCHAR(20) NOT NULL,
    strategy_name VARCHAR(50) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    start_date TIMESTAMP WITH TIME ZONE,
    end_date TIMESTAMP WITH TIME ZONE,
    initial_capital DOUBLE NOT NULL,
    final_equity DOUBLE NOT NULL,
    total_return_pct DOUBLE,
    sharpe_ratio DOUBLE,
    max_drawdown_pct DOUBLE,
    total_trades INT,
    win_rate DOUBLE,
    result_json CLOB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
