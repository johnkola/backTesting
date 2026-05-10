package com.bazarbozorg.backtest;

import com.bazarbozorg.backtest.cli.BacktestCommand;
import com.bazarbozorg.backtest.cli.ImportDataCommand;
import com.bazarbozorg.backtest.cli.ListInstrumentsCommand;
import com.bazarbozorg.backtest.cli.ListStrategiesCommand;
import com.bazarbozorg.backtest.cli.ReportCommand;
import com.bazarbozorg.backtest.cli.TrainCommand;
import picocli.CommandLine;
import picocli.CommandLine.Command;

@Command(name = "backtest",
         mixinStandardHelpOptions = true,
         version = "Backtest 1.0",
         description = "Stock/Forex Backtesting CLI Application",
         subcommands = {
             ImportDataCommand.class,
             ListStrategiesCommand.class,
             ListInstrumentsCommand.class,
             TrainCommand.class,
             BacktestCommand.class,
             ReportCommand.class,
         })
public class BacktestApplication implements Runnable {

    public static void main(String[] args) {
        int exitCode = new CommandLine(new BacktestApplication()).execute(args);
        System.exit(exitCode);
    }

    @Override
    public void run() {
        CommandLine.usage(this, System.out);
    }
}
