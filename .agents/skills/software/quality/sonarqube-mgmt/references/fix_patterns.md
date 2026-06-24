# SonarQube Fix Patterns

This guide provides common fix patterns for issues reported by SonarQube, specifically for Python projects.

## Security Vulnerabilities (Bandit)

### B101: Use of assert detected
- **Problem**: `assert` is for debugging and can be optimized away.
- **Fix**: Use explicit `if` checks and raise exceptions.
- **Example**:
  ```python
  # Bad
  assert user_id is not None
  
  # Good
  if user_id is None:
      raise ValueError("user_id must not be None")
  ```

### B110: Try, Except, Pass
- **Problem**: Swallowing all exceptions makes debugging impossible.
- **Fix**: Log the exception or catch specific ones.
- **Example**:
  ```python
  # Bad
  try:
      do_something()
  except:
      pass
      
  # Good
  try:
      do_something()
  except SpecificError as e:
      logger.error(f"Failed: {e}")
  ```

### B603: Subprocess untrusted input
- **Problem**: Passing unsanitized strings to shell.
- **Fix**: Use `shlex.quote()` and avoid `shell=True`.
- **Example**:
  ```python
  import shlex
  import subprocess
  
  # Bad
  subprocess.run(f"ls {user_input}", shell=True)
  
  # Good
  safe_input = shlex.quote(user_input)
  subprocess.run(["ls", safe_input])
  ```

## Code Smells (Pylint)

### W0718: Catching too general exception
- **Problem**: `except Exception:` catches everything.
- **Fix**: Catch only what you expect.
- **Example**:
  ```python
  # Bad
  try:
      save_file()
  except Exception:
      ...
      
  # Good
  try:
      save_file()
  except IOError:
      ...
  ```

## Maintainability

### Cognitve Complexity
- **Problem**: Function is too hard to understand.
- **Fix**: Break down into smaller, focused functions.
