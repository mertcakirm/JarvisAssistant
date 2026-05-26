import os
import json
import re
import sys
import time
from pathlib import Path
import subprocess

from actions.terminal_control import get_current_dir

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
MODEL_NAME      = "gemini-2.5-flash"

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]

def _get_model(model_name=MODEL_NAME):
    import google.generativeai as genai
    genai.configure(api_key=_get_api_key())
    return genai.GenerativeModel(model_name)

def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\r?\n?", "", text)
    text = re.sub(r"\r?\n?```\s*$", "", text)
    return text.strip()

GEMINI_PROMPT_TEMPLATE = """Sen GEMINI'sin — JARVIS sisteminin geliştirici ajanısın.
Görevin: sana verilen yazılım projesini uçtan uca tamamlamak.

ÇALIŞMA KURALLARI:

1. GÖREV ANALİZİ
Her görevi aldığında önce şunu çıkar:
- Projenin amacı nedir?
- Hangi dosyalar/modüller gerekiyor?
- Bağımlılıklar neler? (kütüphaneler, API'lar, veritabanı)
- Tahmini adım sayısı kaç?

2. ADIM ADIM UYGULA
Her adımı tek tek uygula. Bir adımı bitirmeden diğerine geçme.
Her adımın sonunda şu formatta durum bildirimi ver:

[DURUM RAPORU]
Tamamlanan: <ne yapıldı, tek cümle>
Oluşturulan dosyalar: <dosya listesi>
Sonraki adım: <ne yapılacak>
Kalan adım sayısı: <tahmin>
Sorun: <varsa açıkla, yoksa "Yok">

3. KOD KALİTESİ
- Temiz, okunabilir kod yaz
- Her fonksiyona Türkçe veya İngilizce kısa açıklama ekle
- Hata yönetimini (try/except veya try/catch) her kritik noktaya koy
- Dosya isimlerini ve yapıyı proje başında belirle, sonradan değiştirme
- Kullanılan kütüphaneleri requirements.txt veya package.json'a ekle

4. HATA DURUMU
Bir hata oluştuysa veya devam edemiyorsan şunu yap:
- Ne tür bir hata olduğunu açıkla
- Çözüm önerini belirt
- Kullanıcıdan ek bilgi gerekiyorsa listele

[HATA RAPORU]
Hata: <açıklama>
Nerede: <dosya/fonksiyon>
Öneri: <çözüm>
Gerekli bilgi: <varsa listele>

5. TAMAMLANMA
Tüm adımlar bitince şu formatı kullan:

[PROJE TAMAMLANDI]
Özet: <ne yapıldı, 2-3 cümle>
Dosya yapısı:
  <klasör ve dosyaların listesi>
Çalıştırma komutu: <nasıl başlatılır>
Notlar: <varsa dikkat edilecekler>

6. YASAK DAVRANIŞLAR
- Yarım bırakma — bir şeyi başladıysan bitir
- "Yapamam" deme — alternatif üret
- Gereksiz soru sorma — verilen bilgiyle ilerle, gerçekten eksik varsa sor
- Açıklama yazmak yerine kodu yaz — çalışan kod her zaman önceliklidir

Şu anda sana iletilen görev:

[GÖREV BAŞLANGICI]
{instruction}
[GÖREV SONU]

Current directory: {current_dir}
Files in project:
{files_context}

Provide your plan and the first step's code implementation if applicable, or just the plan if research is needed.
Return ONLY a JSON list of objects with "path" and "content" for the files you are acting on, AND a "report" field containing the required [DURUM RAPORU] or [PROJE TAMAMLANDI] text.
"""

def gemini_cli(parameters: dict, player=None, speak=None, project_mode=False) -> str:
    """
    Autonomous coding agent that works on the current directory.
    Can implement features, fix bugs, and refactor code.
    """
    instruction = parameters.get("instruction", "")
    if not instruction:
        return "Please provide an instruction for the Gemini CLI agent."

    current_dir = get_current_dir()
    
    if player:
        player.write_log(f"[GeminiCLI] Starting task: {instruction[:50]}...")
        if project_mode:
            player.log_project_terminal(f"\n>>> GEMINI DEVELOPER AGENT: {instruction}")
            player.add_project_task("gemini_cli_task", f"Gemini: {instruction[:40]}")
            player.update_project_task("gemini_cli_task", "in_progress")

    if speak and not project_mode:
        speak("Geliştirici ajan görevlendirildi. İşlem başlatılıyor.")

    # 1. Scan the current directory
    files_list = []
    for root, dirs, files in os.walk(current_dir):
        # Ignore common directories
        dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__', 'node_modules', 'venv', '.venv')]
        for file in files:
            rel_path = os.path.relpath(os.path.join(root, file), current_dir)
            files_list.append(rel_path)

    files_context = "\n".join(files_list[:100]) # Limit context

    # 2. Generate content using the new system prompt
    model = _get_model()
    prompt = GEMINI_PROMPT_TEMPLATE.format(
        instruction=instruction,
        current_dir=current_dir,
        files_context=files_context
    )

    # Note: In a real implementation, this would likely involve multiple turns 
    # to follow the "Step-by-Step" rule perfectly, but for this CLI tool 
    # we'll adapt the internal logic to handle the prompt's structured output.
    
    final_report = ""
    max_retries = 5
    for attempt in range(max_retries):
        try:
            import google.generativeai as genai
            generation_config = genai.types.GenerationConfig(
                response_mime_type="application/json"
            )
            response = model.generate_content(prompt, generation_config=generation_config)
            raw_text = _strip_fences(response.text)
            
            # The model is asked to return JSON with path, content and report
            # We need to handle cases where it might just return the report text
            try:
                data = json.loads(raw_text)
                if isinstance(data, list):
                    # Old format fallback or mixed
                    plan = data
                    report = "İşlem devam ediyor..."
                else:
                    plan = data.get("files", [])
                    report = data.get("report", raw_text)
            except:
                # Fallback: Parse markdown-like report and extract code blocks if JSON fails
                report = raw_text
                plan = [] # Manual extraction would be complex here, so we hope for JSON
            
            final_report = report
            
            if player:
                player.log_project_terminal(f"\n{report}")

            # Apply file changes if any
            for item in plan:
                path = item.get("path")
                content = item.get("content")
                if path and content:
                    full_path = current_dir / path
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(content, encoding="utf-8")
                    if player:
                        player.log_project_terminal(f"✓ Dosya güncellendi: {path}")

            break
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                wait_time = 15.0 + (attempt * 10)
                time.sleep(wait_time)
                continue
            final_report = f"Hata: {e}"
            break

    if player and project_mode:
        player.update_project_task("gemini_cli_task", "completed")
    
    if speak and not project_mode:
        speak("Görev tamamlandı.")

    return final_report