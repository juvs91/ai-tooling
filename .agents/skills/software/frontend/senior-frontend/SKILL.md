---
name: senior-frontend
description: Frontend development skill for React, Next.js, TypeScript, and Tailwind CSS. Use when building React components, optimizing Next.js performance, analyzing bundle sizes, implementing accessibility, or reviewing frontend code quality.
origin: alirezarezvani/claude-skills
allowed-tools: Read, Edit, Write, Bash, Glob, Grep
---

# Senior Frontend

Frontend development patterns, performance optimization, and best practices for React/Next.js applications.

## When to Activate

- Building or reviewing React components
- Next.js App Router architecture decisions
- Bundle size analysis and optimization
- Accessibility audits
- TypeScript patterns and type safety
- Tailwind CSS design systems
- Frontend testing strategy

---

## React Patterns

### Compound Components

Share state between related components without prop drilling:

```tsx
const Tabs = ({ children }) => {
  const [active, setActive] = useState(0);
  return (
    <TabsContext.Provider value={{ active, setActive }}>
      {children}
    </TabsContext.Provider>
  );
};
Tabs.List = TabList;
Tabs.Panel = TabPanel;
```

### Custom Hooks

Extract and reuse stateful logic:

```tsx
function useDebounce<T>(value: T, delay = 500): T {
  const [debouncedValue, setDebouncedValue] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debouncedValue;
}
```

### Render Props

```tsx
function DataFetcher({ url, render }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    fetch(url).then(r => r.json()).then(setData).finally(() => setLoading(false));
  }, [url]);
  return render({ data, loading });
}
```

---

## Next.js Optimization

### Server vs Client Components

**Default to Server Components**. Add `'use client'` only when you need:
- Event handlers (`onClick`, `onChange`)
- State (`useState`, `useReducer`)
- Effects (`useEffect`)
- Browser APIs

```tsx
// ✅ Server Component — no directive needed
async function ProductPage({ params }) {
  const product = await getProduct(params.id);
  return (
    <div>
      <h1>{product.name}</h1>
      <AddToCartButton productId={product.id} />
    </div>
  );
}

// Client Component — only when interactive
'use client';
function AddToCartButton({ productId }) {
  const [adding, setAdding] = useState(false);
  return <button onClick={() => addToCart(productId)}>Add</button>;
}
```

### Data Fetching Patterns

```tsx
// ✅ Parallel fetching
async function Dashboard() {
  const [user, stats] = await Promise.all([getUser(), getStats()]);
  return <div>...</div>;
}

// ✅ Streaming with Suspense
async function ProductPage({ params }) {
  return (
    <div>
      <ProductDetails id={params.id} />
      <Suspense fallback={<ReviewsSkeleton />}>
        <Reviews productId={params.id} />
      </Suspense>
    </div>
  );
}
```

### Image Optimization

```tsx
import Image from 'next/image';

// Above the fold — load immediately
<Image src="/hero.jpg" alt="Hero" width={1200} height={600} priority />

// Responsive
<div className="relative aspect-video">
  <Image src="/product.jpg" alt="Product" fill
    sizes="(max-width: 768px) 100vw, 50vw" className="object-cover" />
</div>
```

---

## Bundle Optimization

Heavy packages to avoid or replace:

| Package | Size | Alternative |
|---------|------|-------------|
| moment | 290KB | date-fns (12KB) or dayjs (2KB) |
| lodash | 71KB | lodash-es with tree-shaking |
| axios | 14KB | native fetch or ky (3KB) |
| @mui/material | Large | shadcn/ui or Radix UI |

---

## TypeScript Patterns

```tsx
// Props with children
interface CardProps {
  className?: string;
  children: React.ReactNode;
}

// Generic component
interface ListProps<T> {
  items: T[];
  renderItem: (item: T) => React.ReactNode;
}
function List<T>({ items, renderItem }: ListProps<T>) {
  return <ul>{items.map(renderItem)}</ul>;
}
```

---

## Accessibility Checklist

1. **Semantic HTML**: Use `<button>`, `<nav>`, `<main>`, `<article>`
2. **Keyboard navigation**: all interactive elements focusable
3. **ARIA labels**: for icons and complex widgets
4. **Color contrast**: minimum 4.5:1 for normal text
5. **Focus indicators**: visible focus states with `focus-visible:`

```tsx
<button
  type="button"
  aria-label="Close dialog"
  onClick={onClose}
  className="focus-visible:ring-2 focus-visible:ring-blue-500"
>
  <XIcon aria-hidden="true" />
</button>
```

---

## Testing Strategy

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

test('button triggers action on click', async () => {
  const onClick = vi.fn();
  render(<Button onClick={onClick}>Click me</Button>);
  await userEvent.click(screen.getByRole('button'));
  expect(onClick).toHaveBeenCalledTimes(1);
});
```

---

## Tailwind Utilities

```tsx
import { cn } from '@/lib/utils';

<button className={cn(
  'px-4 py-2 rounded',
  variant === 'primary' && 'bg-blue-500 text-white',
  disabled && 'opacity-50 cursor-not-allowed'
)} />
```
