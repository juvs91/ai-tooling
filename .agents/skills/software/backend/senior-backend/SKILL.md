---
name: senior-backend
description: Backend engineering skill for Python/FastAPI services. Covers SOLID principles, DRY patterns, API design, database optimization, async patterns, error handling, security, and testing. Use when designing or implementing backend services, APIs, or data layers in Python.
origin: alirezarezvani/claude-skills (adapted for Python/FastAPI)
allowed-tools: Read, Edit, Write, Bash, Glob, Grep
---

# Senior Backend Engineer

Python backend patterns, FastAPI best practices, SOLID principles, and production-grade service design.

## When to Activate

- Designing FastAPI endpoints or routers
- Implementing service/repository layers
- Applying SOLID or DRY principles
- Database query optimization (N+1, indexing)
- Error handling and validation strategies
- Authentication / JWT / OAuth patterns
- Background tasks and async patterns
- Rate limiting, caching, middleware

---

## SOLID Principles

### Single Responsibility

Each class/module does ONE thing:

```python
# ❌ God class
class UserService:
    def create_user(self): ...
    def send_email(self): ...
    def generate_pdf_report(self): ...

# ✅ Separated responsibilities
class UserService:
    def create_user(self): ...

class EmailService:
    def send_welcome_email(self, user): ...

class ReportService:
    def generate_user_report(self, user): ...
```

### Open/Closed — Open for extension, closed for modification

```python
from abc import ABC, abstractmethod

class PriceCalculator(ABC):
    @abstractmethod
    def calculate(self, base_price: float) -> float: ...

class RegularPriceCalculator(PriceCalculator):
    def calculate(self, base_price: float) -> float:
        return base_price

class DiscountPriceCalculator(PriceCalculator):
    def __init__(self, discount: float):
        self.discount = discount
    def calculate(self, base_price: float) -> float:
        return base_price * (1 - self.discount)
```

### Dependency Inversion — Depend on abstractions

```python
# ✅ FastAPI dependency injection
class UserRepository(ABC):
    @abstractmethod
    async def get_by_id(self, user_id: str) -> User | None: ...

class PostgresUserRepository(UserRepository):
    def __init__(self, db: AsyncSession):
        self.db = db
    async def get_by_id(self, user_id: str) -> User | None:
        return await self.db.get(User, user_id)

async def get_user_repo(db: AsyncSession = Depends(get_db)) -> UserRepository:
    return PostgresUserRepository(db)

@router.get("/users/{user_id}")
async def get_user(user_id: str, repo: UserRepository = Depends(get_user_repo)):
    return await repo.get_by_id(user_id)
```

---

## DRY — Don't Repeat Yourself

```python
# ❌ Repeated validation logic
@router.post("/products")
async def create_product(data: dict):
    if not data.get("name"):
        raise HTTPException(400, "Name required")
    if len(data["name"]) > 100:
        raise HTTPException(400, "Name too long")

@router.put("/products/{id}")
async def update_product(id: str, data: dict):
    if not data.get("name"):        # duplicated!
        raise HTTPException(400, "Name required")

# ✅ Pydantic model handles validation once
class ProductBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    price: float = Field(..., gt=0)

class ProductCreate(ProductBase): ...
class ProductUpdate(ProductBase): ...
```

---

## FastAPI Patterns

### Router Structure

```python
# routers/products.py
router = APIRouter(prefix="/products", tags=["products"])

@router.get("/", response_model=list[ProductResponse])
async def list_products(
    skip: int = 0,
    limit: int = Query(default=20, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await ProductService(db).list(skip=skip, limit=limit)

@router.post("/", response_model=ProductResponse, status_code=201)
async def create_product(
    data: ProductCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await ProductService(db).create(data, owner_id=current_user.id)
```

### Service Layer

```python
class ProductService:
    def __init__(self, db: AsyncSession):
        self.repo = ProductRepository(db)

    async def list(self, skip: int, limit: int) -> list[Product]:
        return await self.repo.find_many(skip=skip, limit=limit)

    async def create(self, data: ProductCreate, owner_id: str) -> Product:
        product = Product(**data.model_dump(), owner_id=owner_id)
        return await self.repo.save(product)
```

### Error Handling

```python
# Centralized exception handler
@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})

# Custom business exceptions
class ProductNotFoundError(Exception):
    def __init__(self, product_id: str):
        self.product_id = product_id

@app.exception_handler(ProductNotFoundError)
async def product_not_found_handler(request, exc: ProductNotFoundError):
    return JSONResponse(
        status_code=404,
        content={"detail": f"Product {exc.product_id} not found"}
    )
```

---

## Database Patterns

### Avoid N+1 Queries

```python
# ❌ N+1
products = await db.execute(select(Product))
for product in products.scalars():
    category = await db.get(Category, product.category_id)  # N queries!

# ✅ Eager load with joinedload
stmt = select(Product).options(joinedload(Product.category))
products = await db.execute(stmt)
```

### Indexing Strategy

```python
class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(index=True)           # single-column
    category_id: Mapped[str] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(index=True)

    __table_args__ = (
        Index("ix_products_category_created", "category_id", "created_at"),  # composite
    )
```

---

## Async Patterns

```python
# ✅ Parallel async operations
async def get_dashboard_data(user_id: str) -> dict:
    user, orders, stats = await asyncio.gather(
        user_service.get(user_id),
        order_service.list(user_id),
        analytics_service.get_stats(user_id),
    )
    return {"user": user, "orders": orders, "stats": stats}

# Background tasks
@router.post("/orders")
async def create_order(
    data: OrderCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    order = await OrderService(db).create(data)
    background_tasks.add_task(send_order_confirmation, order.id)
    return order
```

---

## Caching

```python
from functools import lru_cache
import redis.asyncio as redis

# Simple in-memory cache for config
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

# Redis cache for data
async def get_product_cached(product_id: str, cache: redis.Redis) -> Product:
    cached = await cache.get(f"product:{product_id}")
    if cached:
        return Product.model_validate_json(cached)

    product = await db.get(Product, product_id)
    await cache.setex(f"product:{product_id}", 300, product.model_dump_json())
    return product
```

---

## Standard Response Format

```python
class APIResponse(BaseModel, Generic[T]):
    data: T
    meta: dict = {}

class ErrorResponse(BaseModel):
    code: str
    message: str
    field: str | None = None
```
