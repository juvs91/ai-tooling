---
name: python-testing
description: Python testing patterns using pytest. Covers fixtures, parameterized tests, mocking, async testing, coverage, and TDD. Use when writing Python tests, diagnosing test failures, improving coverage, or structuring a test suite.
origin: wshobson/agents (python-development plugin)
color: yellow
allowed-tools:
  - Read
  - Edit
  - Write
  - Bash
  - Glob
  - Grep
  - mcp__atlassian__jira_get_issue
  - mcp__atlassian__jira_search
  - mcp__atlassian__jira_add_comment
  - mcp__bitbucket__bb_get
  - mcp__bitbucket__bb_post
  - mcp__squit-remote__squit_search
  - mcp__squit-remote__squit_dependencies
  - mcp__squit-remote__squit_impact
  - mcp__squit-remote__squit_get_code
---

# Python Testing Patterns

Production-grade testing strategies for Python services using pytest.

## When to Activate

- Writing unit or integration tests in Python
- Setting up pytest fixtures and conftest.py
- Mocking external services or databases
- Improving test coverage
- Diagnosing flaky tests
- Async tests (asyncio, FastAPI)
- CI/CD test configuration

---

## Core Principle

**One test = one behavior.** Each test verifies exactly one thing to simplify debugging.

```python
# ❌ Tests multiple behaviors
def test_user():
    user = create_user("alice")
    assert user.name == "alice"
    assert user.is_active == True
    assert send_welcome_email(user) == True  # unrelated!

# ✅ Separate tests
def test_user_name():
    user = create_user("alice")
    assert user.name == "alice"

def test_user_active_by_default():
    user = create_user("alice")
    assert user.is_active is True
```

---

## Basic Patterns (AAA)

```python
# Arrange — Act — Assert
def test_calculate_total():
    # Arrange
    cart = Cart()
    cart.add_item(price=10.0, quantity=2)

    # Act
    total = cart.calculate_total()

    # Assert
    assert total == 20.0

# Exception testing
def test_divide_by_zero():
    with pytest.raises(ZeroDivisionError):
        divide(10, 0)

def test_invalid_user_raises():
    with pytest.raises(ValueError, match="Email is required"):
        create_user(email="")
```

---

## Fixtures

```python
# conftest.py
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

@pytest.fixture
def user():
    return User(id="1", name="Alice", email="alice@example.com")

@pytest.fixture(scope="module")
def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    yield engine
    engine.dispose()

@pytest.fixture
async def db_session(db_engine):
    async with AsyncSession(db_engine) as session:
        yield session
        await session.rollback()

# Use in tests
async def test_create_user(db_session):
    user = await UserService(db_session).create(name="Bob")
    assert user.id is not None
```

---

## Parameterized Tests

```python
@pytest.mark.parametrize("email,valid", [
    ("alice@example.com", True),
    ("bob@test.org", True),
    ("notanemail", False),
    ("@nodomain.com", False),
    ("", False),
])
def test_email_validation(email, valid):
    assert validate_email(email) == valid

@pytest.mark.parametrize("price,discount,expected", [
    (100.0, 0.1, 90.0),
    (100.0, 0.0, 100.0),
    (50.0, 0.5, 25.0),
])
def test_price_discount(price, discount, expected):
    assert apply_discount(price, discount) == pytest.approx(expected)
```

---

## Mocking

```python
from unittest.mock import AsyncMock, MagicMock, patch

# Mock external HTTP call
@patch("services.product.httpx.AsyncClient")
async def test_fetch_price(mock_client):
    mock_client.return_value.__aenter__.return_value.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"price": 99.99}
    )
    price = await fetch_external_price("SKU-001")
    assert price == 99.99

# Mock repository
async def test_create_order():
    mock_repo = AsyncMock()
    mock_repo.save.return_value = Order(id="ord-1", total=150.0)

    service = OrderService(repo=mock_repo)
    order = await service.create(items=[...])

    mock_repo.save.assert_called_once()
    assert order.id == "ord-1"

# Retry behavior
def test_retry_on_failure():
    mock_fn = MagicMock(side_effect=[Exception("fail"), Exception("fail"), "success"])
    result = retry(mock_fn, max_attempts=3)
    assert result == "success"
    assert mock_fn.call_count == 3
```

---

## Async Testing (FastAPI)

```python
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

async def test_create_product(client):
    response = await client.post("/products", json={"name": "Widget", "price": 9.99})
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Widget"

async def test_get_nonexistent_product(client):
    response = await client.get("/products/nonexistent-id")
    assert response.status_code == 404
```

---

## Coverage

```bash
# Run with coverage
pytest --cov=app --cov-report=term-missing --cov-report=html

# Target: 80%+ for business logic, 100% for critical paths
# Exclude: migrations, __init__.py, config files
```

```ini
# pytest.ini / pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "unit: fast isolated tests",
    "integration: tests requiring DB or external services",
    "slow: tests taking >1s",
]

[tool.coverage.run]
omit = ["*/migrations/*", "*/config.py", "*/__init__.py"]
```

---

## Test Structure

```
tests/
├── conftest.py          # shared fixtures
├── unit/
│   ├── test_services.py
│   └── test_models.py
├── integration/
│   ├── test_api.py
│   └── test_db.py
└── fixtures/
    └── data.json
```
