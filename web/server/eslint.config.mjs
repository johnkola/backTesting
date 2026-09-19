import js from '@eslint/js'
import globals from 'globals'
import stylistic from '@stylistic/eslint-plugin'
import importX from 'eslint-plugin-import-x'

// `.mjs` on purpose: this package is CommonJS (no "type" in package.json), so a
// plain eslint.config.js would be loaded as CJS and could not use `import`.
export default [
  { ignores: ['node_modules', 'public'] },
  js.configs.recommended,
  {
    files: ['**/*.js'],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: 'commonjs',
      globals: globals.node,
    },
    plugins: {
      '@stylistic': stylistic,
      'import-x': importX,
    },
    rules: {
      // --- Formatting -------------------------------------------------------
      // The style this service already used. Note these differ from the
      // client's on purpose: the client is ESM without semicolons, this is
      // CommonJS with them, and neither should be bent to match the other.
      '@stylistic/indent': ['error', 2, { SwitchCase: 1, offsetTernaryExpressions: true }],
      '@stylistic/quotes': ['error', 'single', { avoidEscape: true }],
      '@stylistic/semi': ['error', 'always'],
      '@stylistic/comma-dangle': ['error', 'always-multiline'],
      '@stylistic/object-curly-spacing': ['error', 'always'],
      '@stylistic/arrow-parens': ['error', 'always'],
      '@stylistic/space-infix-ops': 'error',
      '@stylistic/keyword-spacing': 'error',
      '@stylistic/comma-spacing': 'error',

      // --- Stray whitespace -------------------------------------------------
      '@stylistic/no-multiple-empty-lines': ['error', { max: 1, maxBOF: 0, maxEOF: 0 }],
      '@stylistic/no-trailing-spaces': 'error',
      '@stylistic/eol-last': ['error', 'always'],
      '@stylistic/padded-blocks': ['error', 'never'],

      // --- Requires ---------------------------------------------------------
      // `import-x/order` does read `require()` calls, so the same grouping holds
      // here as in the client: node builtins and packages first, then this
      // service's own modules. An unused require is an error — always a leftover.
      //
      // `no-duplicates` is kept for any future ESM file but does NOT see a
      // module required twice: it only inspects `import` statements. Verified,
      // not assumed — two `require('./db')` lines pass it.
      'import-x/order': ['error', {
        groups: [['builtin', 'external'], ['internal', 'parent', 'sibling', 'index']],
        'newlines-between': 'never',
      }],
      'import-x/no-duplicates': 'error',
      'no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    },
  },
]
