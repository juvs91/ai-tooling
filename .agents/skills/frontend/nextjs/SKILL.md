---
name: nextjs-skills
description: Next.js App Router patterns, server/client components, dynamic routes, data fetching, and Vercel deployment. Use when working with Next.js 13+ App Router, server components, routing issues, or Vercel AI SDK integration.
origin: wsimmonds/claude-nextjs-skills
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

# Next.js Skills

Focused patterns for Next.js 13+ App Router. Covers the most common errors and anti-patterns.

## When to Activate

- App Router architecture questions
- Server vs Client component confusion
- Dynamic routes and params
- Data fetching patterns
- Vercel AI SDK integration
- Search params with Suspense
- Cookie/header access patterns

---

## Rule #1 — No `any` Types

**TypeScript `any` causes build failures.** Always type explicitly.

```tsx
// ❌
const data: any = await fetch(...).then(r => r.json());

// ✅
interface Product { id: string; name: string; price: number; }
const data: Product = await fetch(...).then(r => r.json());
```

---

## Server vs Client Components

Server Components are the **DEFAULT**. Do NOT add `'use client'` unless you specifically need:

| Need | Solution |
|------|----------|
| `onClick`, `onChange` | `'use client'` |
| `useState`, `useReducer` | `'use client'` |
| `useEffect` | `'use client'` |
| Browser APIs | `'use client'` |
| DB/API access | Server Component (default) |
| cookies(), headers() | Server Component (default) |

```tsx
// ✅ Server Component
async function ProductPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const product = await db.product.findUnique({ where: { id } });
  return <ProductView product={product} />;
}

// ✅ Composing: pass Server Components as children to Client
'use client';
function ClientWrapper({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return <div onClick={() => setOpen(!open)}>{children}</div>;
}
```

---

## App Router Fundamentals

### File Structure

```
app/
├── layout.tsx          # Root layout — MUST have <html> and <body>
├── page.tsx            # Home page
├── loading.tsx         # Loading UI
├── error.tsx           # Error boundary ('use client')
├── not-found.tsx       # 404 page
├── (group)/            # Route group — no URL segment
│   └── page.tsx
├── [slug]/             # Dynamic route
│   └── page.tsx
└── [...slug]/          # Catch-all route
```

### Root Layout — Required Tags

```tsx
// app/layout.tsx
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
```

### Metadata

```tsx
// Static
export const metadata: Metadata = {
  title: 'My App',
  description: 'Description',
};

// Dynamic
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const product = await getProduct(params.id);
  return { title: product.name };
}
```

---

## Dynamic Routes & Params

In Next.js 15+, `params` is a **Promise** — always await it:

```tsx
// ✅ Next.js 15+
export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <div>Product: {id}</div>;
}

// generateStaticParams for static generation
export async function generateStaticParams() {
  const products = await getProducts();
  return products.map((p) => ({ id: p.id }));
}
```

---

## Search Params + Suspense

`useSearchParams()` **must** be wrapped in `<Suspense>`:

```tsx
// ✅
function SearchResults() {
  const searchParams = useSearchParams();
  const query = searchParams.get('q');
  return <div>Results for: {query}</div>;
}

export default function Page() {
  return (
    <Suspense fallback={<div>Loading...</div>}>
      <SearchResults />
    </Suspense>
  );
}
```

---

## Server Navigation

```tsx
import { redirect, notFound } from 'next/navigation';

export default async function Page({ params }) {
  const { id } = await params;
  const item = await getItem(id);

  if (!item) notFound();
  if (!item.published) redirect('/');

  return <div>{item.title}</div>;
}
```

---

## Client Cookie Pattern

```tsx
'use client';
import Cookies from 'js-cookie';

function ThemeToggle() {
  const setTheme = (theme: string) => {
    Cookies.set('theme', theme, { expires: 365 });
    document.documentElement.classList.toggle('dark', theme === 'dark');
  };
  return <button onClick={() => setTheme('dark')}>Dark</button>;
}
```

---

## Common Anti-Patterns to Avoid

```tsx
// ❌ Fetching in Client Component effects
'use client';
function ProductList() {
  const [products, setProducts] = useState([]);
  useEffect(() => { fetch('/api/products').then(...) }, []);
}

// ✅ Fetch in Server Component
async function ProductList() {
  const products = await getProducts();
  return <ul>{products.map(p => <li key={p.id}>{p.name}</li>)}</ul>;
}
```

```tsx
// ❌ Unnecessary 'use client'
'use client';
function StaticCard({ title }: { title: string }) {
  return <div>{title}</div>; // No interactivity needed!
}

// ✅ Just a Server Component
function StaticCard({ title }: { title: string }) {
  return <div>{title}</div>;
}
```
