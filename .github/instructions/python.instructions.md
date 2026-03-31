---
description: "Python coding style guidelines following PEP 8 and modern best practices"
applyTo: "**/*.py"
---

# MCP Server Development Guidelines

## Coding standards overview

This instruction file enforces Python coding standards based
on PEP 8, type hints, and modern Python practices.

### Programming Language: Python

**Python Best Practices:**
- Follow PEP 8 style guidelines strictly
- Use type hints for all function parameters and return values
- Prefer f-strings for string formatting over older methods
- Use descriptive variable and function names
- Implement proper error handling with specific exception types
- Use virtual environments for dependency management

### Code Style: Clean Code

**Clean Code Principles:**
- Write self-documenting code with meaningful names
- Keep functions small and focused on a single responsibility
- Avoid deep nesting and complex conditional statements
- Use consistent formatting and indentation
- Write code that tells a story and is easy to understand
- Refactor ruthlessly to eliminate code smells

### Testing: PyTest

**Testing Guidelines:**
- Tests are in `src/docshub/tests/`
- Write comprehensive unit tests for all business logic
- Test behaviors, not implementation
- Follow the AAA pattern: Arrange, Act, Assert — but do **not** annotate sections
  with `# Arrange`, `# Act`, `# Assert` comments; structure should be self-evident
  from the code
- Maintain good test coverage (aim for 80%+ for critical paths)
- Write descriptive test names that explain the expected behavior
- Use test doubles (mocks, stubs, spies) appropriately
- Implement integration tests for API endpoints and user flows
- Keep tests fast, isolated, and deterministic

### AI Code Generation Preferences

When generating code, please:

- Generate complete, working code examples with proper imports
- Include inline comments for complex logic and business rules
- Follow the established patterns and conventions in this project
- Suggest improvements and alternative approaches when relevant
- Consider performance, security, and maintainability
- Include error handling and edge case considerations
- Generate appropriate unit tests when creating new functions
- Follow accessibility best practices for UI components
- Use semantic HTML and proper ARIA attributes when applicable

## Specific examples of coding practices

<!-- Adapted from https://github.com/duthaho/copilot-instructions -->

Use the specific examples in the following sections to inform
coding style decisions.

### Code Formatting

Use **Black** for automatic formatting and **Ruff** for linting.

```toml
# pyproject.toml
[tool.black]
line-length = 100
target-version = ['py311']

[tool.ruff]
line-length = 100
target-version = "py311"
select = ["E", "F", "I", "N", "W", "UP"]
```

### Type Hints

**MANDATORY**: All public functions, methods, and classes must have type hints.

✅ **Good:**

```python
from typing import Optional, List
from uuid import UUID

def get_customer_orders(customer_id: UUID, limit: int = 10) -> List[Order]:
    """Retrieve customer orders with type hints."""
    pass

class OrderService:
    def __init__(self, repository: OrderRepository) -> None:
        self._repository = repository

    def create_order(self, customer_id: UUID) -> Order:
        return Order(id=uuid4(), customer_id=customer_id)
```

❌ **Bad:**

```python
def get_customer_orders(customer_id, limit=10):  # No type hints
    pass
```

### Naming Conventions

- **Classes**: PascalCase (`OrderService`, `CustomerRepository`)
- **Functions/Methods**: snake_case (`calculate_total`, `get_by_id`)
- **Constants**: UPPER_SNAKE_CASE (`MAX_RETRIES`, `DEFAULT_TIMEOUT`)
- **Private attributes**: Leading underscore (`_internal_state`)
- **Module names**: snake_case (`order_service.py`)

```python
# Good naming
class OrderProcessor:
    MAX_BATCH_SIZE = 100

    def __init__(self) -> None:
        self._cache: dict[str, Order] = {}

    def process_orders(self, orders: List[Order]) -> None:
        pass
```

### Docstrings

Use Google-style docstrings for public APIs:

```python
def calculate_discount(
    base_price: float,
    discount_percentage: float,
    customer_tier: str
) -> float:
    """Calculate discounted price based on customer tier.

    Args:
        base_price: The original price before discount
        discount_percentage: Discount percentage (0-100)
        customer_tier: Customer tier ("REGULAR", "PREMIUM", "VIP")

    Returns:
        The final price after applying discount

    Raises:
        ValueError: If discount_percentage is not between 0 and 100

    Example:
        >>> calculate_discount(100.0, 10.0, "PREMIUM")
        90.0
    """
    if not 0 <= discount_percentage <= 100:
        raise ValueError("Discount must be between 0 and 100")

    return base_price * (1 - discount_percentage / 100)
```

### Dataclasses and Pydantic

Prefer dataclasses for domain models and Pydantic for API schemas:

```python
from dataclasses import dataclass, field
from pydantic import BaseModel, Field

# Domain model
@dataclass
class Order:
    id: UUID
    items: List[OrderItem] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)

# API schema
class CreateOrderRequest(BaseModel):
    customer_id: str = Field(..., description="Customer UUID")
    items: List[OrderItemSchema] = Field(default_factory=list)

    class Config:
        json_schema_extra = {
            "example": {
                "customer_id": "123e4567-e89b-12d3-a456-426614174000",
                "items": []
            }
        }
```

### Error Handling

Use specific exceptions and context managers:

```python
# Custom exceptions
class OrderError(Exception):
    """Base exception for order-related errors."""
    pass

class OrderNotFoundError(OrderError):
    """Raised when order is not found."""

    def __init__(self, order_id: UUID):
        self.order_id = order_id
        super().__init__(f"Order {order_id} not found")

# Context managers
from contextlib import contextmanager
from typing import Generator

@contextmanager
def transaction(session: Session) -> Generator[Session, None, None]:
    """Database transaction context manager."""
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

# Usage
with transaction(session) as tx:
    repository.save(order)
```

### Imports

Organize imports in this order:

1. Standard library
2. Third-party packages
3. Local application imports

Use isort or ruff for automatic sorting:

```python
# Standard library
import os
from datetime import datetime
from typing import List, Optional
from uuid import UUID, uuid4

# Third-party
from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
import pytest

# Local
from domain.order import Order, OrderItem
from application.repositories import OrderRepository
from infrastructure.database import get_db_session
```

### Function and Method Design

- **Keep functions small** (< 20 lines when possible)
- **Single responsibility**
- **Pure functions** when possible (no side effects)

```python
# Good: small, focused functions
def calculate_tax(amount: float, tax_rate: float) -> float:
    return amount * tax_rate

def calculate_total_with_tax(subtotal: float, tax_rate: float) -> float:
    tax = calculate_tax(subtotal, tax_rate)
    return subtotal + tax

# Bad: too many responsibilities
def process_order(order_data: dict) -> dict:
    # Validates, calculates, saves, sends email - too much!
    pass
```

### Type Aliases

Use type aliases for complex types:

```python
from typing import TypeAlias, Dict, List
from uuid import UUID

UserId: TypeAlias = UUID
OrderId: TypeAlias = UUID
ItemsMap: TypeAlias = Dict[str, List[OrderItem]]

def get_user_orders(user_id: UserId) -> List[Order]:
    pass
```

### Protocols (Interfaces)

Use Protocol for dependency inversion:

```python
from typing import Protocol, Optional

class Repository[T](Protocol):
    """Generic repository protocol."""

    def get_by_id(self, id: UUID) -> Optional[T]:
        ...

    def save(self, entity: T) -> None:
        ...

    def delete(self, id: UUID) -> None:
        ...

# Implementation
class SqlAlchemyOrderRepository:
    def get_by_id(self, id: UUID) -> Optional[Order]:
        # Concrete implementation
        pass
```

### Constants and Enums

Use Enums for related constants:

```python
from enum import Enum, auto

class OrderStatus(Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"

class PaymentMethod(Enum):
    CREDIT_CARD = auto()
    PAYPAL = auto()
    BANK_TRANSFER = auto()

# Usage
order.status = OrderStatus.CONFIRMED
```

### List/Dict Comprehensions

Use comprehensions for simple transformations:

```python
# Good: readable comprehension
active_orders = [order for order in orders if order.status == "ACTIVE"]

order_ids = [order.id for order in orders]

order_map = {order.id: order for order in orders}

# Bad: too complex, use regular loop
result = [
    process_order(order) if order.status == "PENDING"
    else update_order(order) if order.status == "ACTIVE"
    else cancel_order(order)
    for order in orders if order.customer_id in active_customers
]  # Too complex!
```

### f-strings

Use f-strings for string formatting:

```python
# Good
name = "John"
age = 30
message = f"Hello {name}, you are {age} years old"

# Debug formatting
value = 42
print(f"{value=}")  # Outputs: value=42

# Bad
message = "Hello %s, you are %d years old" % (name, age)  # Old style
message = "Hello {}, you are {} years old".format(name, age)  # Verbose
```

### Comments

Write self-documenting code. Use comments sparingly for "why", not "what".
Never add structural markers or section labels that merely restate what the code
already shows (e.g. `# Arrange`, `# Act`, `# Assert`, `# Get the customer`):

```python
# Bad: states the obvious
# Get the customer by ID
customer = repository.get_by_id(customer_id)

# Bad: structural label with no informational value
# Arrange
orders = []

# Good: explains non-obvious reasoning
# Use cached value to avoid expensive database call during checkout
customer = self._cache.get(customer_id) or repository.get_by_id(customer_id)
```

### Line Length

Maximum 100 characters per line (configurable in black):

```python
# Good: readable within line limit
def create_order(
    customer_id: UUID,
    items: List[OrderItem],
    shipping_address: str,
    billing_address: str
) -> Order:
    pass

# Good: break long strings
message = (
    "This is a very long message that needs to be split "
    "across multiple lines for readability"
)
```

## Validation Checklist

- [ ] All public functions have type hints
- [ ] Code is formatted with Black
- [ ] Imports are organized (stdlib, third-party, local)
- [ ] Docstrings for public APIs
- [ ] Enums for related constants
- [ ] Dataclasses for data structures
- [ ] Type aliases for complex types
- [ ] No lines exceed 100 characters
- [ ] Meaningful variable names
- [ ] No commented-out code in commits
- [ ] Tests follow AAA pattern
- [ ] Unit tests have no external dependencies
- [ ] Test names are descriptive
- [ ] Coverage is above 80%
- [ ] All tests pass before committing
- [ ] Documentation (README.md) updated
