import java.io.FileWriter;
import java.io.PrintWriter;
import java.time.DayOfWeek;
import java.time.LocalDate;
import java.util.Random;

public class GenerateTestData {
    public static void main(String[] args) throws Exception {
        Random rng = new Random(42);
        LocalDate start = LocalDate.of(2020, 1, 2);
        LocalDate end = LocalDate.of(2023, 12, 29);
        String outputPath = "test-data/AAPL_daily.csv";

        try (PrintWriter pw = new PrintWriter(new FileWriter(outputPath))) {
            pw.println("Date,Open,High,Low,Close,Volume");

            double prevClose = 75.00;
            int count = 0;

            for (LocalDate d = start; !d.isAfter(end); d = d.plusDays(1)) {
                DayOfWeek dow = d.getDayOfWeek();
                if (dow == DayOfWeek.SATURDAY || dow == DayOfWeek.SUNDAY) continue;

                double openFactor = 1.0 + (rng.nextDouble() * 0.02 - 0.01);
                double openPrice = prevClose * openFactor;

                double closeFactor = 1.0 + (rng.nextDouble() * 0.04 - 0.02);
                double closePrice = openPrice * closeFactor;

                double maxOC = Math.max(openPrice, closePrice);
                double minOC = Math.min(openPrice, closePrice);

                double highPrice = maxOC * (1.0 + Math.abs(rng.nextDouble() * 0.01));
                double lowPrice = minOC * (1.0 - Math.abs(rng.nextDouble() * 0.01));

                int volume = 50000000 + rng.nextInt(100000001);

                openPrice = Math.round(openPrice * 100.0) / 100.0;
                highPrice = Math.round(highPrice * 100.0) / 100.0;
                lowPrice = Math.round(lowPrice * 100.0) / 100.0;
                closePrice = Math.round(closePrice * 100.0) / 100.0;

                pw.printf("%s,%.2f,%.2f,%.2f,%.2f,%d%n", d, openPrice, highPrice, lowPrice, closePrice, volume);

                prevClose = closePrice;
                count++;
            }
            System.out.println("Generated " + count + " trading days of data");
            System.out.println("Output: " + outputPath);
        }
    }
}
