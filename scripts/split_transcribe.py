#!/home/admin/miniconda3/bin/python
"""分片转录长音频"""
import subprocess, json, os, time, sys, requests

STT_API = 'http://127.0.0.1:9977/api'
FFMPEG = '/home/admin/miniconda3/envs/stt/bin/ffmpeg'
FFPROBE = '/home/admin/miniconda3/envs/stt/bin/ffprobe'

def get_duration(filepath):
    r = subprocess.run([FFPROBE, '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', filepath], capture_output=True, text=True)
    return float(r.stdout.strip())

def split_and_transcribe(filepath, video_id, outdir, chunk_sec=600):
    os.makedirs(outdir, exist_ok=True)
    output_file = os.path.join(outdir, f'{video_id}_转录.txt')
    
    duration = get_duration(filepath)
    chunks = int(duration / chunk_sec) + 1
    print(f"Duration: {duration:.0f}s, {chunks} chunks of {chunk_sec}s")
    
    all_text = []
    
    for i in range(chunks):
        start = i * chunk_sec
        chunk_file = f'/tmp/{video_id}_chunk{i}.mp3'
        
        # Split
        cmd = [FFMPEG, '-i', filepath, '-ss', str(start), '-t', str(chunk_sec), '-acodec', 'libmp3lame', '-q:a', '5', chunk_file, '-y']
        subprocess.run(cmd, capture_output=True, timeout=60)
        
        if not os.path.exists(chunk_file) or os.path.getsize(chunk_file) < 100:
            print(f"Chunk {i}: split failed")
            continue
        
        # Transcribe
        print(f"Chunk {i+1}/{chunks}: transcribing...")
        try:
            with open(chunk_file, 'rb') as f:
                resp = requests.post(STT_API, data={'language': 'zh', 'model': 'base', 'response_format': 'text'}, files={'file': f}, timeout=600)
            if resp.status_code == 200:
                d = resp.json()
                text = d.get('data', '')
                if text:
                    all_text.append(text)
                    print(f"Chunk {i+1}: {len(text)} chars")
        except Exception as e:
            print(f"Chunk {i+1}: error {e}")
        
        os.remove(chunk_file)
        time.sleep(2)
    
    full_text = '\n'.join(all_text)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(full_text)
    print(f"Saved {len(full_text)} chars to {output_file}")
    return full_text

if __name__ == '__main__':
    filepath = sys.argv[1]
    video_id = sys.argv[2]
    outdir = sys.argv[3]
    split_and_transcribe(filepath, video_id, outdir)
