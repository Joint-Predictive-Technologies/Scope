// Playwright end-to-end tests for the Scope homepage & influence flows (§16).
//
// These are not run in the Python sandbox — they require a running server and
// Playwright browsers:
//
//   npm i -D @playwright/test && npx playwright install
//   BASE_URL=http://localhost:8000 npx playwright test tests/e2e/scope.spec.js
//
// (or point BASE_URL at the deployed Railway URL for a smoke run.)

const { test, expect } = require('@playwright/test');

const BASE = process.env.BASE_URL || 'http://localhost:8000';

test.describe('Scope homepage', () => {
  test('no raw alert text above <nav>', async ({ page }) => {
    await page.goto(BASE);
    const nav = page.locator('nav').first();
    const beforeNav = await page.evaluate(() => {
      const nav = document.querySelector('nav');
      let html = '';
      for (const n of document.body.childNodes) {
        if (n === nav) break;
        html += n.textContent || '';
      }
      return html;
    });
    expect(beforeNav).not.toMatch(/RULE_\d/);
  });

  test('conveyor is single semantic list (duplicate is aria-hidden)', async ({ page }) => {
    await page.goto(BASE);
    await page.waitForTimeout(1500);
    const hidden = await page.locator('#tape .tape-track[aria-hidden="true"]').count();
    const visible = await page.locator('#tape .tape-track:not([aria-hidden])').count();
    expect(visible).toBeLessThanOrEqual(1);
    expect(hidden).toBeLessThanOrEqual(1);
  });

  test('Today tabs update in place and set URL param', async ({ page }) => {
    await page.goto(BASE);
    await page.click('.today-tab[data-tab="congress"]');
    await expect(page).toHaveURL(/tab=congress/);
    await expect(page.locator('.today-tab[data-tab="congress"]')).toHaveClass(/active/);
  });

  test('rule rail expands on click and ESC collapses', async ({ page }) => {
    await page.goto(BASE);
    await page.click('.rail-chip[data-rule="RULE_06"]');
    await expect(page.locator('#rail-detail .rail-detail-name')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('#rail-detail .rail-detail-inner')).toHaveCount(0);
  });

  test('evidence drawer opens from a Today signal and closes on Escape', async ({ page }) => {
    await page.goto(BASE);
    await page.waitForSelector('.today-row', { timeout: 8000 });
    await page.locator('.today-row').first().click();
    await expect(page.locator('#ev-backdrop.open')).toBeVisible();
    await expect(page).toHaveURL(/ev=/);
    await page.keyboard.press('Escape');
    await expect(page.locator('#ev-backdrop.open')).toHaveCount(0);
  });

  test('corroboration card progressive disclosure', async ({ page }) => {
    await page.goto(BASE);
    const expand = page.locator('.corr-expand').first();
    if (await expand.count()) {
      await expand.click();
      await expect(expand).toHaveText(/Hide detail/);
    }
  });

  test('heat map date-basis toggle switches modes', async ({ page }) => {
    await page.goto(BASE);
    await page.click('.hm-mode[data-mode="ingestion"]');
    await expect(page.locator('.hm-mode[data-mode="ingestion"]')).toHaveClass(/active/);
  });

  test('no section stuck on Loading after 10s', async ({ page }) => {
    await page.goto(BASE);
    await page.waitForTimeout(10000);
    const stuck = await page.locator('text=/Loading…/').count();
    expect(stuck).toBe(0);
  });
});

test.describe('Influence / AIPAC', () => {
  test('lobbying page resolves AIPAC via deep link', async ({ page }) => {
    await page.goto(`${BASE}/lobbying?org=AIPAC`);
    await expect(page.locator('#entity-panel')).toContainText('American Israel Public Affairs Committee');
    await expect(page.locator('#entity-panel')).toContainText('Senate LDA');
    // Campaign finance must be labeled, not fabricated.
    await expect(page.locator('#entity-panel')).toContainText(/not ingested/i);
  });

  test('command palette surfaces AIPAC as an organization', async ({ page }) => {
    await page.goto(BASE);
    await page.keyboard.press('Meta+k').catch(() => {});
    await page.fill('#cmd-input', 'aipac');
    await page.waitForTimeout(500);
    await expect(page.locator('#cmd-results')).toContainText(/American Israel|Organizations/i);
  });
});

test.describe('Mobile', () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test('evidence drawer renders as bottom sheet', async ({ page }) => {
    await page.goto(BASE);
    await page.waitForSelector('.today-row', { timeout: 8000 });
    await page.locator('.today-row').first().click();
    const box = await page.locator('#ev-drawer').boundingBox();
    // bottom sheet: full width
    expect(box.width).toBeGreaterThan(360);
  });
});
