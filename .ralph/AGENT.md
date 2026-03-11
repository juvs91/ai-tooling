# Agent Configuration

## Project Type
{{PROJECT_TYPE}} (e.g., SQL, Python, TypeScript, Rust)

## Build Commands
{{BUILD_COMMANDS}}
<!-- Example: No build commands — this project modifies SQL files only. -->
<!-- Example: npm run build -->
<!-- Example: cargo build -->

## Test Commands
{{TEST_COMMANDS}}
<!-- Example: No automated tests — validation is visual review. -->
<!-- Example: npm test -->
<!-- Example: pytest -->

## Validation
After each change, verify:
{{VALIDATION_CHECKLIST}}
<!-- Example for SQL:
1. UNION ALL branches have same column count and names
2. PostgreSQL syntax inside EXTERNAL_QUERY
3. BigQuery syntax outside EXTERNAL_QUERY
-->
<!-- Example for Python:
1. No import errors
2. Type hints consistent
3. Tests pass
-->
