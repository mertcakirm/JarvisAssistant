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

def _safe_relative_path(base_dir: Path, rel_path: str) -> Path:
    """Return a path inside base_dir or raise if Gemini tries to write outside."""
    full_path = (base_dir / rel_path).resolve()
    base_resolved = base_dir.resolve()
    if base_resolved not in full_path.parents and full_path != base_resolved:
        raise ValueError(f"Unsafe path outside project: {rel_path}")
    return full_path

def _collect_project_context(current_dir: Path) -> str:
    ignored_dirs = {".git", "__pycache__", "node_modules", "venv", ".venv", "dist", "build"}
    text_exts = {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".json", ".md",
        ".txt", ".yml", ".yaml", ".toml", ".ini", ".sh",
    }
    blocks = []
    total_chars = 0
    max_chars = 20000

    for root, dirs, files in os.walk(current_dir):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        for file_name in sorted(files):
            full_path = Path(root) / file_name
            rel_path = os.path.relpath(full_path, current_dir)
            if full_path.suffix.lower() not in text_exts:
                blocks.append(f"- {rel_path}")
                continue

            try:
                content = full_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                blocks.append(f"- {rel_path} (read failed)")
                continue

            snippet = content[:2500]
            block = f"\n--- FILE: {rel_path} ---\n{snippet}"
            if len(content) > len(snippet):
                block += "\n... [truncated]"

            if total_chars + len(block) > max_chars:
                blocks.append("\n... [project context truncated]")
                return "\n".join(blocks)

            blocks.append(block)
            total_chars += len(block)

    return "\n".join(blocks)

GEMINI_PROMPT_TEMPLATE = """Sen GEMINI'sin — JARVIS sisteminin geliştirici ajanısın.
JARVIS bu işte PROJE YÖNETİCİSİDİR. Sen kodu üretir, dosyaları değiştirir,
durumu raporlar ve eksik kalan işleri açıkça bildirirsin. JARVIS senin
güncellemelerini takip eder, kullanıcı isteklerine göre seni tekrar yönlendirir
ve proje kapsamı tam bitene kadar süreci yönetir.

Görevin: sana verilen yazılım projesini veya değişiklik isteğini uçtan uca tamamlamak.

ÇALIŞMA KURALLARI:

1. GÖREV ANALİZİ
Her görevi aldığında önce şunu çıkar:
- Projenin amacı nedir?
- Hangi dosyalar/modüller gerekiyor?
- Bağımlılıklar neler? (kütüphaneler, API'lar, veritabanı)
- Tahmini adım sayısı kaç?

2. ADIM ADIM UYGULA
Bu çağrıda uygulanabilecek en doğru ve güvenli adımı uygula. Bir adımı
bitirmeden diğerine geçme. Her çağrının sonunda şu formatta durum bildirimi ver:

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
Project context:
{project_context}

Return ONLY valid JSON with this exact shape:
{{
  "status": "in_progress" | "completed" | "blocked",
  "files": [
    {{"path": "relative/path.ext", "content": "complete file content"}}
  ],
  "report": "Required [DURUM RAPORU], [HATA RAPORU], or [PROJE TAMAMLANDI] text",
  "next_instruction": "If status is in_progress, the next concise instruction JARVIS should send back to Gemini. Empty when completed or blocked."
}}

Rules for JSON:
- Use relative file paths only.
- Include complete file contents for every file you modify.
- If no file change is needed, use "files": [].
- Do not include markdown, code fences, or comments outside JSON.
"""

def gemini_cli(parameters: dict, player=None, speak=None, project_mode=False) -> str:
    """
    Autonomous coding agent that works on the current directory.
    Can implement features, fix bugs, and refactor code.
    """
    instruction = parameters.get("instruction", "")
    if not instruction:
        return "Please provide an instruction for the Gemini CLI agent."

    requested_dir = parameters.get("working_dir") or parameters.get("project_dir")
    current_dir = Path(requested_dir).expanduser().resolve() if requested_dir else get_current_dir()
    if not current_dir.exists() or not current_dir.is_dir():
        return f"Hata: Çalışma dizini bulunamadı: {current_dir}"
    task_id = f"gemini_cli_task_{int(time.time() * 1000) % 100000}"
    
    if player:
        player.write_log(f"[GeminiCLI] Starting task: {instruction[:50]}...")
        if project_mode:
            player.log_project_terminal(f"\n>>> GEMINI DEVELOPER AGENT: {instruction}")
            player.add_project_task(task_id, f"Gemini: {instruction[:40]}")
            player.update_project_task(task_id, "in_progress")

    if speak and not project_mode:
        speak("Geliştirici ajan görevlendirildi. İşlem başlatılıyor.")

    project_context = _collect_project_context(current_dir)

    # 2. Generate content using the new system prompt
    model = _get_model()
    prompt = GEMINI_PROMPT_TEMPLATE.format(
        instruction=instruction,
        current_dir=current_dir,
        project_context=project_context
    )

    # Note: In a real implementation, this would likely involve multiple turns 
    # to follow the "Step-by-Step" rule perfectly, but for this CLI tool 
    # we'll adapt the internal logic to handle the prompt's structured output.
    
    final_report = ""
    final_status = "blocked"
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
                    plan = data
                    report = "İşlem devam ediyor..."
                    status = "in_progress"
                    next_instruction = ""
                else:
                    plan = data.get("files", [])
                    report = data.get("report", raw_text)
                    status = data.get("status", "in_progress")
                    next_instruction = data.get("next_instruction", "")
            except:
                # Fallback: Parse markdown-like report and extract code blocks if JSON fails
                report = raw_text
                plan = []
                status = "blocked"
                next_instruction = ""
            
            final_report = report
            final_status = status
            if next_instruction:
                final_report += f"\n\n[JARVIS NEXT]\n{next_instruction}"
            
            if player:
                player.log_project_terminal(f"\n{report}")
                if status == "in_progress" and next_instruction:
                    player.log_project_terminal(f"\nNext Gemini instruction: {next_instruction}")

            # Apply file changes if any
            for item in plan:
                path = item.get("path")
                content = item.get("content")
                if path and content is not None:
                    full_path = _safe_relative_path(current_dir, path)
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
            final_status = "blocked"
            break

    if player and project_mode:
        task_status = "failed" if final_status == "blocked" or final_report.lower().startswith("hata:") else "completed"
        player.update_project_task(task_id, task_status)
    
    if speak and not project_mode:
        speak("Görev tamamlandı.")

    return final_report
