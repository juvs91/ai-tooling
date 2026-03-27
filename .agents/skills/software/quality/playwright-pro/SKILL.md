---
name: playwright-pro
description: Production-grade Playwright E2E testing. Use when writing, fixing, or reviewing Playwright tests, diagnosing flaky tests, migrating from Cypress/Selenium, or setting up test CI/CD pipelines.
origin: alirezarezvani/claude-skills
allowed-tools: Read, Edit, Write, Bash, Glob, Grep
---

# Playwright Pro

Production-grade E2E testing toolkit for Playwright.

## When to Activate

- Writing new Playwright tests
- Diagnosing flaky or failing tests
- Migrating from Cypress or Selenium
- Setting up CI/CD for E2E tests
- Code review of existing test suites
- BrowserStack or TestRail integration

---

## Core Principles (Non-Negotiable)

1. **Semantic locators** over CSS/XPath — use `getByRole`, `getByLabel`, `getByText`
2. **Web-first assertions** — replace arbitrary `await page.waitForTimeout(1000)` with `await expect(locator).toBeVisible()`
3. **Test isolation** — no shared state between tests
4. **Base URL in config** — never hardcode URLs in tests
5. **Retry strategy** — 2 retries in CI, 0 locally

---

## Project Setup

```typescript
// playwright.config.ts
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [['html'], ['list']],
  use: {
    baseURL: process.env.BASE_URL ?? 'http://localhost:3000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['iPhone 13'] } },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:3000',
    reuseExistingServer: !process.env.CI,
  },
});
```

---

## Locator Patterns

```typescript
// ✅ Semantic locators (preferred)
page.getByRole('button', { name: 'Submit' })
page.getByLabel('Email address')
page.getByPlaceholder('Search...')
page.getByText('Welcome back')
page.getByTestId('product-card')

// ❌ Avoid — brittle
page.locator('.btn-submit')
page.locator('#email-input')
page.locator('div > span:nth-child(2)')
```

---

## Assertions

```typescript
// ✅ Web-first assertions — auto-retry until condition met
await expect(page.getByRole('heading')).toBeVisible();
await expect(page.getByRole('button', { name: 'Save' })).toBeEnabled();
await expect(page.getByRole('status')).toHaveText('Saved successfully');
await expect(page).toHaveURL('/dashboard');
await expect(page).toHaveTitle(/Dashboard/);

// ❌ Avoid
await page.waitForTimeout(2000);  // arbitrary wait
const text = await page.textContent('.message');
expect(text).toBe('Saved');       // not web-first
```

---

## Page Object Model

```typescript
// pages/LoginPage.ts
export class LoginPage {
  readonly page: Page;
  readonly emailInput: Locator;
  readonly passwordInput: Locator;
  readonly submitButton: Locator;
  readonly errorMessage: Locator;

  constructor(page: Page) {
    this.page = page;
    this.emailInput = page.getByLabel('Email');
    this.passwordInput = page.getByLabel('Password');
    this.submitButton = page.getByRole('button', { name: 'Sign in' });
    this.errorMessage = page.getByRole('alert');
  }

  async goto() {
    await this.page.goto('/login');
  }

  async login(email: string, password: string) {
    await this.emailInput.fill(email);
    await this.passwordInput.fill(password);
    await this.submitButton.click();
  }
}

// tests/auth.spec.ts
test('successful login redirects to dashboard', async ({ page }) => {
  const loginPage = new LoginPage(page);
  await loginPage.goto();
  await loginPage.login('user@example.com', 'password123');
  await expect(page).toHaveURL('/dashboard');
});
```

---

## Fixtures for Reuse

```typescript
// fixtures.ts
import { test as base } from '@playwright/test';
import { LoginPage } from './pages/LoginPage';

type Fixtures = { loginPage: LoginPage; authenticatedPage: Page };

export const test = base.extend<Fixtures>({
  loginPage: async ({ page }, use) => {
    await use(new LoginPage(page));
  },
  authenticatedPage: async ({ page }, use) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill('admin@example.com');
    await page.getByLabel('Password').fill('password');
    await page.getByRole('button', { name: 'Sign in' }).click();
    await page.waitForURL('/dashboard');
    await use(page);
  },
});
```

---

## Test Templates

### Authentication Flow

```typescript
test.describe('Authentication', () => {
  test('login with valid credentials', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill('user@example.com');
    await page.getByLabel('Password').fill('password123');
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page).toHaveURL('/dashboard');
    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
  });

  test('shows error for invalid credentials', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill('wrong@example.com');
    await page.getByLabel('Password').fill('wrongpassword');
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('alert')).toHaveText('Invalid credentials');
    await expect(page).toHaveURL('/login');
  });
});
```

### CRUD Operations

```typescript
test('create and delete a product', async ({ authenticatedPage: page }) => {
  await page.goto('/products/new');
  await page.getByLabel('Name').fill('Test Product');
  await page.getByLabel('Price').fill('99.99');
  await page.getByRole('button', { name: 'Create' }).click();

  await expect(page.getByRole('status')).toHaveText('Product created');
  await expect(page).toHaveURL(/\/products\/\w+/);

  await page.getByRole('button', { name: 'Delete' }).click();
  await page.getByRole('button', { name: 'Confirm' }).click();
  await expect(page).toHaveURL('/products');
});
```

---

## Diagnosing Flaky Tests

```typescript
// ❌ Common flakiness causes
await page.click('.submit');           // no wait for element ready
await page.waitForTimeout(1000);       // arbitrary sleep

// ✅ Fixes
await page.getByRole('button', { name: 'Submit' }).click(); // auto-waits
await expect(locator).toBeEnabled();   // wait for state
await page.waitForLoadState('networkidle'); // wait for network
```

---

## CI Configuration

```yaml
# .github/workflows/e2e.yml
- name: Install Playwright browsers
  run: npx playwright install --with-deps chromium

- name: Run E2E tests
  run: npx playwright test
  env:
    BASE_URL: ${{ secrets.STAGING_URL }}
    CI: true

- uses: actions/upload-artifact@v4
  if: failure()
  with:
    name: playwright-report
    path: playwright-report/
```
