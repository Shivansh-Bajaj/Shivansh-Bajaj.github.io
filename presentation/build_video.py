import asyncio
import os
from pathlib import Path
import edge_tts
from moviepy import ImageClip, AudioFileClip, concatenate_videoclips
from playwright.async_api import async_playwright

# Voice selection: Indian English (Male: "en-IN-PrabhatNeural", Female: "en-IN-NeerjaNeural")
VOICE = "en-IN-PrabhatNeural"

SLIDES_DATA = [
    {
        "slide_index": 0,
        "text": "This is Visheshak — Sanskrit for 'the discerner.' It's an autonomous options trading agent built for the Alpaca hackathon, and it's built around one uncomfortable observation about AI trading."
    },
    {
        "slide_index": 1,
        "text": "Most AI trading demos hand the language model the keys and hope. We bet the opposite way. LLMs are genuinely good at reading a market regime — synthesizing price action and headlines into 'this looks rangebound.' They should never be trusted to write an order ticket. So we built a system where the model has judgment, but code has power."
    },
    {
        "slide_index": 2,
        "text": "Four roles, strictly separated. The Scout — pure code — prices every candidate trade from live option chains and builds a menu. The Strategist — the LLM — reads the market brief and a news digest, and may only pick items off that menu, or abstain. It cannot invent a strike, a price, or a quantity; the API response is a constrained schema. Then the Risk Officer — pure deterministic code, no LLM anywhere near it — holds absolute veto: it clamps sizes, blocks entries, and can kill the whole book. Only then does the Executor touch orders."
    },
    {
        "slide_index": 3,
        "text": "The strategy itself: sell time with defined risk. Short-dated credit spreads and iron condors on SPY and QQQ — structures where the maximum loss is known before entry — chosen by a regime playbook: uptrend, put credit spread; downtrend, call credit spread; rangebound, iron condor; unclear, do nothing. Plus at most two small momentum trades on mega-caps."
    },
    {
        "slide_index": 4,
        "text": "Every safety rule lives in code, not in the prompt. One percent max loss per structure. Ten percent total open risk. A daily loss halt. An equity kill floor that flattens everything. Blackouts around macro releases. And a news storm gate that stands down on any name with an unusual headline burst. Our design test for every prompt rule: what happens if the LLM ignores it? The answer must always be — a worse trade inside the limits, or no trade. Never money at risk beyond limits."
    },
    {
        "slide_index": 5,
        "text": "Every failure path falls toward flat. If the model fails, a fallback chain of models tries next — and two bad answers mean abstain. If an order won't fill, the executor walks away rather than leaving stale orders queued. And if the infrastructure itself dies, the agent rebuilds its position state from the broker on restart — this journal entry is from our real deployment, where it recovered a live iron condor after a server rebuild. It never trades blind."
    },
    {
        "slide_index": 6,
        "text": "And here it is, live. The dashboard shows the portfolio, and — more importantly — each structure the way a trader thinks about it: the credit we received, what it costs to close now, and how much buffer remains to the short strikes. Below, the equity curve, straight from the journal. And this is the journal itself — every market brief, every LLM rationale, every risk verdict, every order, appended in real time. The whole reasoning trail is auditable after the fact."
    },
    {
        "slide_index": 7,
        "text": "Visheshak. Judgment from the model, discipline from the code — autonomous since the opening bell. Thank you."
    }
]

OUTPUT_DIR = Path("build_artifacts")
OUTPUT_DIR.mkdir(exist_ok=True)

async def capture_slides():
    """Renders slides.html and captures full-resolution screenshots for each slide index."""
    html_path = Path("slides.html").resolve().as_uri()
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        
        for item in SLIDES_DATA:
            idx = item["slide_index"]
            url = f"{html_path}?slide={idx}"
            await page.goto(url)
            await page.wait_for_timeout(500)  # Wait for CSS rise animation to settle
            img_path = OUTPUT_DIR / f"slide_{idx}.png"
            await page.screenshot(path=str(img_path))
            print(f"[1/3] Captured Slide {idx + 1}/{len(SLIDES_DATA)}")
            
        await browser.close()

async def generate_voiceovers():
    """Generates Indian English audio narration clips using edge-tts."""
    for idx, item in enumerate(SLIDES_DATA):
        audio_path = OUTPUT_DIR / f"audio_{idx}.mp3"
        communicate = edge_tts.Communicate(item["text"], VOICE, rate="-2%")
        await communicate.save(str(audio_path))
        print(f"[2/3] Generated Voiceover {idx + 1}/{len(SLIDES_DATA)}")

def assemble_video(output_filename="visheshak_presentation.mp4"):
    """Combines captured slide images with voiceover audio into an MP4 video (MoviePy v2 compatible)."""
    clips = []
    for idx, item in enumerate(SLIDES_DATA):
        img_path = str(OUTPUT_DIR / f"slide_{idx}.png")
        audio_path = str(OUTPUT_DIR / f"audio_{idx}.mp3")
        
        audio_clip = AudioFileClip(audio_path)
        total_slide_duration = audio_clip.duration + 0.4  # Natural pause between slides
        
        video_clip = (
            ImageClip(img_path)
            .with_duration(total_slide_duration)
            .with_audio(audio_clip)
        )
        clips.append(video_clip)
        
    print("[3/3] Rendering video via FFmpeg...")
    final_video = concatenate_videoclips(clips, method="compose")
    final_video.write_videofile(
        output_filename,
        fps=24,
        codec="libx264",
        audio_codec="aac"
    )
    print(f"\nCompleted: Video generated successfully at {output_filename}")

async def main():
    await capture_slides()
    await generate_voiceovers()
    assemble_video()

if __name__ == "__main__":
    asyncio.run(main())