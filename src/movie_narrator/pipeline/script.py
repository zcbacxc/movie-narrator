import time

from ..models import Context, ScriptSegment
from ..utils.prompts import SCRIPT_PROMPT
from ..utils.llm import get_llm_client
from ..utils.json_parser import extract_json

MOCK_SEGMENTS = [
    "一个过气车神突然复出了。",
    "所有人都觉得他疯了。",
    "可他只想再赢一次。",
    "因为热爱，从来不会过期。",
]


def generate_script(ctx: Context) -> Context:
    for attempt in range(3):
        try:
            llm = get_llm_client()
            prompt = SCRIPT_PROMPT.format(
                movie=ctx.movie_name,
                style=ctx.style,
                duration=ctx.duration,
            )
            response = llm.client.chat.completions.create(
                model=llm.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=2048,
            )
            raw = response.choices[0].message.content
            data = extract_json(raw)
            raw_segments = data.get("segments", [])
            segments = []
            for item in raw_segments:
                if isinstance(item, str):
                    segments.append(ScriptSegment(text=item))
                elif isinstance(item, dict) and "text" in item:
                    segments.append(ScriptSegment(text=item["text"]))
            if not segments:
                raise ValueError("empty script from LLM")
            ctx.segments = segments
            return ctx
        except Exception as e:
            if attempt == 2:
                print(f"LLM fallback (3 attempts failed): {e}")
                ctx.segments = [ScriptSegment(text=s) for s in MOCK_SEGMENTS]
                return ctx
            time.sleep(1.5)
    return ctx
