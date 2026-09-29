import asyncio
import os
import shutil
from playwright.async_api import async_playwright

async def record_demo():
    artifact_dir = "/config/.gemini/antigravity/brain/d1459d5d-98f1-4da2-ad80-9fb57bfc8248"
    video_dir = os.path.join(artifact_dir, "demo_video_raw")
    os.makedirs(video_dir, exist_ok=True)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            record_video_dir=video_dir,
            record_video_size={"width": 1280, "height": 720}
        )
        
        page = await context.new_page()
        print("Navigating to local frontend...")
        await page.goto("http://127.0.0.1:8080/")
        await page.wait_for_timeout(2000)
        
        # 1. Prompt 1: What items are currently in my pantry?
        print("Submitting Prompt 1...")
        input_el = page.locator("#input")
        await input_el.fill("What items are currently in my pantry?")
        await page.wait_for_timeout(1000)
        await page.click("button.send-btn")
        
        # Wait for agent response
        print("Waiting for response 1...")
        await page.wait_for_selector(".msg-row.agent", timeout=45000)
        await page.wait_for_timeout(5000)
        
        # 2. Prompt 2: Suggest a recipe with my ingredients and generate an image of it
        print("Submitting Prompt 2...")
        await input_el.fill("Suggest a recipe using my ingredients and generate an image of it")
        await page.wait_for_timeout(1000)
        await page.click("button.send-btn")
        
        # Wait for agent response 2 (includes tool call + image generation)
        print("Waiting for response 2...")
        await page.wait_for_function("document.querySelectorAll('.msg-row.agent').length >= 2", timeout=90000)
        await page.wait_for_timeout(6000)
        
        video_path = await page.video.path()
        await context.close()
        await browser.close()
        
        print("Recorded raw video at:", video_path)
        dest_video = os.path.join(artifact_dir, "agent_demo.webm")
        shutil.copy(video_path, dest_video)
        print("Copied demo video to artifact path:", dest_video)

if __name__ == "__main__":
    asyncio.run(record_demo())
