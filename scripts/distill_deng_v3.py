#!/usr/bin/env python3
"""Distill Deng Xiaoping v3 profile from existing materials using Zhipu/SiliconFlow API"""
import json, os, re, sys, time, gc
from pathlib import Path
from datetime import datetime

# API configs
ZHIPU_KEY = '19649c91787b4220b809353ce50657ad.kHqGtEeuidt6UecO'
ZHIPU_URL = 'https://open.bigmodel.cn/api/paas/v4/chat/completions'
SF_KEY = 'sk-tnaasdpeozarapwsxndsewyobdlvmwauwoemzmwcvltbapmj'
SF_URL = 'https://api.siliconflow.cn/v1/chat/completions'

PERSON_DIR = Path('/home/tearsyu/.openclaw/workspace/think-tank/personalities/military/邓小平')
TEMPLATE_PATH = Path('/home/tearsyu/.openclaw/workspace/think-tank/templates/distill_prompt_v3.md')
MAT_DIR = PERSON_DIR / 'materials'


def call_llm(prompt, model='glm-5', timeout=180):
    """Call Zhipu API with fallback to SiliconFlow"""
    # Try Zhipu first
    try:
        r = __import__('requests').post(ZHIPU_URL, json={
            'model': model,
            'messages': [{'role': 'user', 'content': prompt}],
            'temperature': 0.2,
            'max_tokens': 8000
        }, headers={
            'Authorization': f'Bearer {ZHIPU_KEY}',
            'Content-Type': 'application/json'
        }, timeout=timeout)
        if r.status_code == 200:
            return r.json()['choices'][0]['message']['content'], model
        else:
            print(f"  Zhipu error {r.status_code}: {r.text[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"  Zhipu error: {e}", file=sys.stderr)

    # Fallback
    try:
        r = __import__('requests').post(SF_URL, json={
            'model': 'deepseek-ai/DeepSeek-V3',
            'messages': [{'role': 'user', 'content': prompt}],
            'temperature': 0.2,
            'max_tokens': 8000
        }, headers={
            'Authorization': f'Bearer {SF_KEY}',
            'Content-Type': 'application/json'
        }, timeout=timeout)
        if r.status_code == 200:
            return r.json()['choices'][0]['message']['content'], 'deepseek-v3'
    except Exception as e:
        print(f"  SiliconFlow error: {e}", file=sys.stderr)
    return None, None


def parse_json_response(text):
    if not text:
        return None
    m = re.search(r'```json\s*\n(.*?)\n```', text, re.DOTALL)
    raw = m.group(1) if m else text
    s, e = raw.find('{'), raw.rfind('}')
    if s >= 0 and e > s:
        raw = raw[s:e+1]
    raw = re.sub(r'"([^"]+)"\s*：', r'"\1":', raw)
    raw = re.sub(r',\s*([\]}])', r'\1', raw)
    try:
        return json.loads(raw)
    except:
        pass
    try:
        import ast
        # Try to fix common issues
        raw = re.sub(r'(?<!\\)"(?=[^":,]*[\u4e00-\u9fff])', r'\\"', raw)
        return json.loads(raw)
    except:
        return None


def main():
    # Load v3 template
    v3_template = TEMPLATE_PATH.read_text(encoding='utf-8')
    print(f"Template loaded: {len(v3_template)} chars")

    # Collect all materials
    materials = sorted(MAT_DIR.glob('*.txt'))
    print(f"Found {len(materials)} material files")

    # Build raw materials - use head+tail for large files
    raw_parts = []
    for txt in materials:
        try:
            content = txt.read_text(encoding='utf-8', errors='replace')
            if len(content.strip()) < 50:
                continue
            if len(content) > 6000:
                excerpt = content[:3000] + '\n...[中间省略]...\n' + content[-3000:]
            else:
                excerpt = content
            raw_parts.append(f"## {txt.name}\n{excerpt}")
        except Exception as e:
            print(f"  Error reading {txt.name}: {e}")

    raw_materials = '\n\n'.join(raw_parts)
    print(f"Raw materials total: {len(raw_materials)} chars")

    # Fill template
    prompt = v3_template.replace('{count}', str(len(materials))).replace('{raw_materials}', raw_materials)
    print(f"Final prompt: {len(prompt)} chars")

    # Call LLM
    print("Calling Zhipu GLM-5 for distillation...")
    response, model = call_llm(prompt, model='glm-5', timeout=240)

    if not response:
        print("FAIL: API returned nothing", file=sys.stderr)
        sys.exit(1)

    # Save raw response for debugging
    debug_path = PERSON_DIR / '_distill_v3_raw.txt'
    debug_path.write_text(response, encoding='utf-8')
    print(f"Raw response saved: {len(response)} chars [{model}]")

    # Parse JSON
    result = parse_json_response(response)
    if not result:
        print("FAIL: Could not parse JSON from response", file=sys.stderr)
        sys.exit(1)

    # Ensure logical_tracing exists
    if 'logical_tracing' not in result:
        result['logical_tracing'] = {
            "summary": "", "lineage": {"inherited": [], "transformed": [], "ruptured": [], "converged": []},
            "turning_points": [], "grounding": {"embodied": [], "contextual": []},
            "collaborations": [], "renunciations": [], "adversaries": [],
            "tension_explanation": {"note": "", "tracing": ""}
        }

    # Ensure lacan_drives preserved from v2
    old_v2 = PERSON_DIR / 'personality_v2_backup.json'
    if old_v2.exists():
        try:
            old = json.loads(old_v2.read_text(encoding='utf-8'))
            if 'lacan_drives' in old and 'lacan_drives' not in result:
                result['lacan_drives'] = old['lacan_drives']
        except:
            pass

    result['_distill_meta'] = {
        'source_count': len(materials),
        'model': model,
        'timestamp': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'version': 'v3-logical-tracing'
    }

    # Backup old v2 first
    v2_path = PERSON_DIR / 'personality_v2.json'
    if v2_path.exists():
        backup = PERSON_DIR / 'personality_v2_backup.json'
        if not backup.exists():
            v2_path.rename(backup)
        else:
            v2_path.unlink()

    # Save new v3
    with open(v2_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n✅ V3 distillation complete!")
    print(f"   Domains: {result.get('domains', [])}")
    print(f"   Brief: {result.get('brief', '')}")
    print(f"   Turning points: {len(result.get('logical_tracing', {}).get('turning_points', []))}")
    print(f"   Lineage inherited: {len(result.get('logical_tracing', {}).get('lineage', {}).get('inherited', []))}")
    print(f"   Saved to: {v2_path}")


if __name__ == '__main__':
    main()
