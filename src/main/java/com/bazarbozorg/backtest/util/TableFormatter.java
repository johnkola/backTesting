package com.bazarbozorg.backtest.util;

import java.util.List;

/**
 * Utility class for formatting data as ASCII tables for console output.
 */
public final class TableFormatter {

    private TableFormatter() {
        // Utility class - prevent instantiation
    }

    /**
     * Formats data as an ASCII table with auto-calculated column widths.
     * Numeric values are right-aligned; other values are left-aligned.
     *
     * @param headers the column headers
     * @param rows    the table rows (each row is a list of cell values)
     * @return the formatted ASCII table as a string
     */
    public static String formatTable(List<String> headers, List<List<String>> rows) {
        if (headers == null || headers.isEmpty()) {
            return "";
        }

        int columnCount = headers.size();

        // Determine which columns are numeric (all non-header values look numeric)
        boolean[] numericColumns = new boolean[columnCount];
        for (int col = 0; col < columnCount; col++) {
            numericColumns[col] = isColumnNumeric(rows, col);
        }

        // Calculate maximum width for each column
        int[] colWidths = new int[columnCount];
        for (int col = 0; col < columnCount; col++) {
            colWidths[col] = headers.get(col).length();
        }
        for (List<String> row : rows) {
            for (int col = 0; col < columnCount && col < row.size(); col++) {
                String cell = row.get(col) != null ? row.get(col) : "";
                colWidths[col] = Math.max(colWidths[col], cell.length());
            }
        }

        StringBuilder sb = new StringBuilder();

        // Build separator line
        String separator = buildSeparator(colWidths);

        // Top border
        sb.append(separator).append('\n');

        // Header row
        sb.append(formatRow(headers, colWidths, new boolean[columnCount])); // Headers are always left-aligned
        sb.append('\n');

        // Header separator
        sb.append(separator).append('\n');

        // Data rows
        for (List<String> row : rows) {
            sb.append(formatRow(row, colWidths, numericColumns));
            sb.append('\n');
        }

        // Bottom border
        sb.append(separator);

        return sb.toString();
    }

    /**
     * Builds a separator line like "+--------+--------+--------+".
     */
    private static String buildSeparator(int[] colWidths) {
        StringBuilder sb = new StringBuilder("+");
        for (int width : colWidths) {
            sb.append("-".repeat(width + 2));
            sb.append('+');
        }
        return sb.toString();
    }

    /**
     * Formats a single row with proper alignment and padding.
     */
    private static String formatRow(List<String> cells, int[] colWidths, boolean[] rightAlign) {
        StringBuilder sb = new StringBuilder("|");
        for (int col = 0; col < colWidths.length; col++) {
            String cell = (col < cells.size() && cells.get(col) != null) ? cells.get(col) : "";
            int width = colWidths[col];

            if (rightAlign[col]) {
                // Right-align: pad on the left
                sb.append(' ');
                sb.append(String.format("%" + width + "s", cell));
                sb.append(' ');
            } else {
                // Left-align: pad on the right
                sb.append(' ');
                sb.append(String.format("%-" + width + "s", cell));
                sb.append(' ');
            }
            sb.append('|');
        }
        return sb.toString();
    }

    /**
     * Determines if all values in a column look numeric.
     * A column is considered numeric if every non-empty cell can be parsed as a number.
     */
    private static boolean isColumnNumeric(List<List<String>> rows, int col) {
        if (rows.isEmpty()) {
            return false;
        }

        boolean hasValues = false;
        for (List<String> row : rows) {
            if (col >= row.size()) {
                continue;
            }
            String cell = row.get(col);
            if (cell == null || cell.isBlank()) {
                continue;
            }
            hasValues = true;
            if (!isNumeric(cell)) {
                return false;
            }
        }
        return hasValues;
    }

    /**
     * Checks if a string looks like a numeric value.
     * Supports integers, decimals, negatives, percentages, and values with commas.
     */
    private static boolean isNumeric(String str) {
        if (str == null || str.isBlank()) {
            return false;
        }
        String cleaned = str.trim();
        // Remove trailing percent sign
        if (cleaned.endsWith("%")) {
            cleaned = cleaned.substring(0, cleaned.length() - 1).trim();
        }
        // Remove commas (thousand separators)
        cleaned = cleaned.replace(",", "");

        if (cleaned.isEmpty()) {
            return false;
        }

        try {
            Double.parseDouble(cleaned);
            return true;
        } catch (NumberFormatException e) {
            return false;
        }
    }
}
