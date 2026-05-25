import asyncio
from playwright.async_api import async_playwright
import os

async def main():
    async with async_playwright() as p:
        try:
            safari_profile = os.path.expanduser("~/.jarvis_profiles/safari")
            os.makedirs(safari_profile, exist_ok=True)
            ctx = await p.webkit.launch_persistent_context(safari_profile, headless=False, no_viewport=True)
            print("Context launched. Pages:", ctx.pages)
            if not ctx.pages:
                page = await ctx.new_page()
            else:
                page = ctx.pages[0]
            await page.goto("https://www.google.com")
            print("Title:", await page.title())
            await ctx.close()
        except Exception as e:
            print(f"Error: {e}")

asyncio.run(main())
