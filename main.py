import os
import sys
import subprocess
import numpy as np
import wave
import json
import requests
import threading
from datetime import datetime
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.uix.switch import Switch
from kivy.uix.spinner import Spinner
from kivy.graphics import Color, Rectangle, RoundedRectangle
from kivy.core.window import Window
from kivy.animation import Animation
from kivy.clock import Clock
from kivy.metrics import dp

TG_TOKEN = ""
TG_CHAT = ""
CLIP_DURATION = 30
CLIP_BEFORE = 15
TOP_CLIPS = 10
VOLUME_THRESHOLD = 0.75
SCREAM_RATIO = 3.0
MIN_GAP = 45
OUTPUT_DIR = "/storage/emulated/0/TwitchClips"
USE_AI = True
POLLINATIONS_URL = "https://text.pollinations.ai/"

os.makedirs(OUTPUT_DIR, exist_ok=True)
Window.clearcolor = (0.04, 0.04, 0.08, 1)

SYSTEM_PROMPT = "Ты эксперт по DBD и Outlast Trials. Выбери 10 лучших моментов. Категории: maniac, survivor, funny, epic. Отвечай JSON: {\"highlights\": [{\"time\": 123, \"score\": 9, \"category\": \"funny\", \"reason\": \"Скример\"}]}"

def ask_ai(prompt):
    try:
        resp = requests.get(f"{POLLINATIONS_URL}{prompt}", timeout=30)
        if resp.status_code == 200:
            return resp.text.strip()
    except:
        pass
    return None

def ai_analyze_peaks(peaks):
    if not peaks:
        return []
    desc = "Моменты:\n"
    for p in peaks:
        t = "скример" if p.get("sharp") else "громкий звук"
        desc += f"- {p['time']}с: {t} ({p['volume']:.2f})\n"
    resp = ask_ai(f"{SYSTEM_PROMPT}\n\n{desc}\n\nJSON:")
    if not resp:
        return []
    try:
        s = resp.find("{")
        e = resp.rfind("}") + 1
        if s >= 0 and e > s:
            return json.loads(resp[s:e]).get("highlights", [])
    except:
        pass
    return []

def classify_by_sound(peak):
    if peak.get("sharp"):
        return "funny", "Скример / крик"
    if peak["volume"] > 0.9:
        return "epic", "Эпичный момент"
    if peak["volume"] > 0.8:
        return "maniac", "Момент за маньяка"
    return "survivor", "Момент за выжившего"

def extract_audio(video_path):
    wav = "/tmp/audio.wav"
    try:
        subprocess.run(["ffmpeg", "-y", "-i", video_path, "-ac", "1", "-ar", "16000", wav], capture_output=True, timeout=600)
        return wav if os.path.exists(wav) and os.path.getsize(wav) > 1000 else None
    except:
        return None

def analyze_volume(wav_path):
    try:
        with wave.open(wav_path, "rb") as w:
            sr = w.getframerate()
            frames = w.readframes(w.getnframes())
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
    except:
        return []
    spw = sr
    results, prev = [], 0
    for i in range(0, len(audio) - spw, spw):
        chunk = audio[i:i + spw]
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        sharp = prev > 0 and rms / prev > SCREAM_RATIO
        results.append({"time": i // sr, "volume": rms, "sharp": sharp})
        prev = rms
    if results:
        mx = max(r["volume"] for r in results)
        if mx > 0:
            for r in results:
                r["volume"] /= mx
    return results

def find_peaks(volume_data):
    peaks, last = [], -MIN_GAP * 1000
    for i, r in enumerate(volume_data):
        if (r["volume"] >= VOLUME_THRESHOLD or r["sharp"]) and (i - last) >= MIN_GAP:
            peaks.append(r)
            last = i
    return peaks

def cut_clip(video_path, start, duration, output):
    try:
        subprocess.run(["ffmpeg", "-y", "-ss", str(start), "-i", video_path, "-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", output], capture_output=True, timeout=600)
        return os.path.exists(output)
    except:
        return False

def add_overlay(inp, out, channel, reason="", category="epic"):
    ch = channel.replace(":", "").replace("'", "").replace("\\", "")[:30]
    rs = reason.replace(":", "").replace("'", "").replace("\\", "")[:50]
    colors = {"maniac": "#FF3B3B", "survivor": "#3B9EFF", "funny": "#FFD700", "epic": "#9B59B6"}
    color = colors.get(category, "#FFFFFF")
    filt = ("[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black[bg];"
            f"[bg]drawtext=text='{ch}':fontcolor=white:fontsize=64:x=(w-text_w)/2:y=80:box=1:boxcolor=black@0.6:boxborderw=20[n];"
            f"[n]drawtext=text='{rs}':fontcolor={color}:fontsize=40:x=(w-text_w)/2:y=h-220:box=1:boxcolor=black@0.7:boxborderw=15[f]")
    try:
        subprocess.run(["ffmpeg", "-y", "-i", inp, "-filter_complex", filt, "-map", "[f]", "-map", "0:a?", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", out], capture_output=True, timeout=600)
        return os.path.exists(out)
    except:
        return False

def process_video(video_path, channel_name="Twitch", progress_cb=None):
    def report(t, p):
        if progress_cb:
            progress_cb(t, p)
    if not os.path.exists(video_path):
        report("Файл не найден", 0)
        return []
    report("Аудио...", 5)
    wav = extract_audio(video_path)
    if not wav:
        report("Ошибка аудио", 0)
        return []
    report("Анализ...", 20)
    vol_data = analyze_volume(wav)
    if not vol_data:
        report("Ошибка", 0)
        return []
    report("Поиск...", 35)
    peaks = find_peaks(vol_data)
    if not peaks:
        report("Не найдено", 0)
        return []
    peaks_sorted = sorted(peaks, key=lambda x: (x["sharp"], x["volume"]), reverse=True)[:TOP_CLIPS * 2]
    highlights = []
    if USE_AI:
        report("AI...", 45)
        ai_res = ai_analyze_peaks(peaks_sorted)
        if ai_res:
            highlights = ai_res
    if not highlights:
        for p in peaks_sorted[:TOP_CLIPS]:
            cat, reason = classify_by_sound(p)
            highlights.append({"time": p["time"], "score": int(p["volume"] * 10), "category": cat, "reason": reason})
    highlights.sort(key=lambda x: x["time"])
    highlights = highlights[:TOP_CLIPS]
    report(f"Клипы {len(highlights)}...", 55)
    clips = []
    total = len(highlights)
    for i, h in enumerate(highlights):
        start = max(0, h["time"] - CLIP_BEFORE)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        raw = os.path.join(OUTPUT_DIR, f"raw_{i}_{ts}.mp4")
        final = os.path.join(OUTPUT_DIR, f"clip_{i+1}_{ts}.mp4")
        if cut_clip(video_path, start, CLIP_DURATION, raw):
            add_overlay(raw, final, channel_name, h.get("reason", ""), h.get("category", "epic"))
            try:
                os.remove(raw)
            except:
                pass
            clips.append(final)
        report(f"Клип {i+1}/{total}", 55 + int(45 * (i+1) / total))
    try:
        os.remove(wav)
    except:
        pass
    report(f"Готово! {len(clips)}", 100)
    return clips

def tg_send(file_path, caption=""):
    if not TG_TOKEN or not TG_CHAT:
        return False
    try:
        with open(file_path, "rb") as f:
            requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendVideo", files={"video": f}, data={"chat_id": TG_CHAT, "caption": caption}, timeout=60)
        return True
    except:
        return False

class GradientBackground(FloatLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas.before:
            Color(0.04, 0.04, 0.08, 1)
            self.rect = Rectangle(size=self.size, pos=self.pos)
        self.bind(size=self._update, pos=self._update)
    def _update(self, *args):
        self.rect.size = self.size
        self.rect.pos = self.pos

class RoundedButton(Button):
    def __init__(self, bg_color=(0.35, 0.28, 0.9, 1), **kwargs):
        super().__init__(**kwargs)
        self.background_normal = ""
        self.background_color = (0, 0, 0, 0)
        self.color = (1, 1, 1, 1)
        self.font_size = dp(16)
        self.bold = True
        with self.canvas.before:
            self.color_instr = Color(*bg_color)
            self.rect = RoundedRectangle(size=self.size, pos=self.pos, radius=[dp(14)])
        self.bind(size=self._update, pos=self._update)
    def _update(self, *args):
        self.rect.size = self.size
        self.rect.pos = self.pos

class ClipperApp(App):
    def build(self):
        self.title = "Twitch Clipper"
        self.video_path = None
        self.auto_tg = False
        self.use_ai = USE_AI
        root = GradientBackground()
        main = BoxLayout(orientation='vertical', padding=[dp(20), dp(30), dp(20), dp(20)], spacing=dp(12))
        main.add_widget(Label(text="[b]TWITCH CLIPPER[/b]", markup=True, font_size=dp(32), color=(1, 0.78, 0.2, 1), size_hint=(1, 0.10)))
        main.add_widget(Label(text="DBD • Outlast Trials • AI", font_size=dp(12), color=(0.6, 0.6, 0.8, 1), size_hint=(1, 0.05)))
        gb = BoxLayout(size_hint=(1, 0.08), spacing=dp(10))
        gb.add_widget(Label(text="Игра:", font_size=dp(14), color=(0.8, 0.8, 0.9, 1), size_hint=(0.3, 1)))
        self.game = Spinner(text='Dead by Daylight', values=('Dead by Daylight', 'Outlast Trials', 'Другая'), size_hint=(0.7, 1), background_color=(0.2, 0.2, 0.35, 1), color=(1, 0.9, 0.5, 1), font_size=dp(14))
        gb.add_widget(self.game)
        main.add_widget(gb)
        cb = BoxLayout(size_hint=(1, 0.08), spacing=dp(10))
        cb.add_widget(Label(text="Канал:", font_size=dp(14), color=(0.8, 0.8, 0.9, 1), size_hint=(0.3, 1)))
        self.channel = TextInput(text="Twitch", multiline=False, font_size=dp(14), size_hint=(0.7, 1), background_color=(0.12, 0.12, 0.2, 1), foreground_color=(1, 1, 1, 1), cursor_color=(1, 0.78, 0.2, 1), padding=[dp(12), dp(8)])
        cb.add_widget(self.channel)
        main.add_widget(cb)
        sw = BoxLayout(size_hint=(1, 0.08), spacing=dp(10))
        aib = BoxLayout(spacing=dp(5))
        aib.add_widget(Label(text="AI", font_size=dp(13), color=(0.8, 0.8, 0.9, 1)))
        self.ai_sw = Switch(active=True)
        self.ai_sw.bind(active=self.toggle_ai)
        aib.add_widget(self.ai_sw)
        sw.add_widget(aib)
        tgb = BoxLayout(spacing=dp(5))
        tgb.add_widget(Label(text="TG", font_size=dp(13), color=(0.8, 0.8, 0.9, 1)))
        self.tg_sw = Switch(active=False)
        self.tg_sw.bind(active=self.toggle_tg)
        tgb.add_widget(self.tg_sw)
        sw.add_widget(tgb)
        main.add_widget(sw)
        self.btn_sel = RoundedButton(text="ВЫБРАТЬ ВИДЕО", size_hint=(1, 0.10), bg_color=(0.2, 0.3, 0.5, 1))
        self.btn_sel.bind(on_press=self.open_file)
        main.add_widget(self.btn_sel)
        self.btn_proc = RoundedButton(text="НАРЕЗАТЬ", size_hint=(1, 0.10), bg_color=(0.55, 0.25, 0.9, 1))
        self.btn_proc.bind(on_press=self.start)
        main.add_widget(self.btn_proc)
        self.progress = ProgressBar(max=100, value=0, size_hint=(1, 0.03))
        main.add_widget(self.progress)
        self.status = Label(text="Готов", font_size=dp(13), color=(0.6, 0.9, 0.6, 1), size_hint=(1, 0.06))
        main.add_widget(self.status)
        main.opacity = 0
        Animation(opacity=1, duration=0.8).start(main)
        root.add_widget(main)
        return root
    def toggle_ai(self, inst, val):
        global USE_AI
        USE_AI = val
    def toggle_tg(self, inst, val):
        self.auto_tg = val
    def open_file(self, inst):
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(10))
        chooser = FileChooserListView(filters=['*.mp4', '*.mkv', '*.mov', '*.avi'], path='/storage/emulated/0')
        content.add_widget(chooser)
        bb = BoxLayout(size_hint=(1, 0.15), spacing=dp(10))
        ok = RoundedButton(text="ОК", bg_color=(0.3, 0.7, 0.4, 1))
        cc = RoundedButton(text="X", bg_color=(0.7, 0.3, 0.3, 1))
        popup = Popup(title="Видео", content=content, size_hint=(0.95, 0.95), background_color=(0.1, 0.1, 0.15, 1), title_color=(1, 0.78, 0.2, 1))
        def sel(x):
            if chooser.selection:
                self.video_path = chooser.selection[0]
                self.status.text = f"OK {os.path.basename(self.video_path)[:30]}"
                self.status.color = (0.5, 1, 0.5, 1)
            popup.dismiss()
        ok.bind(on_press=sel)
        cc.bind(on_press=popup.dismiss)
        bb.add_widget(ok)
        bb.add_widget(cc)
        content.add_widget(bb)
        popup.open()
    def start(self, inst):
        if not self.video_path:
            self.status.text = "Выбери видео!"
            self.status.color = (1, 0.5, 0.5, 1)
            return
        self.btn_proc.disabled = True
        self.btn_sel.disabled = True
        threading.Thread(target=self.thread_proc, daemon=True).start()
    def thread_proc(self):
        def cb(t, p):
            Clock.schedule_once(lambda dt: self.upd(t, p))
        clips = process_video(self.video_path, self.channel.text or "Twitch", progress_cb=cb)
        if self.auto_tg and clips:
            for c in clips:
                tg_send(c, "Клип")
        Clock.schedule_once(lambda dt: self.fin(clips))
    def upd(self, t, p):
        self.status.text = t
        self.progress.value = p
    def fin(self, clips):
        if clips:
            self.status.text = f"Готово! {len(clips)}"
            self.status.color = (0.4, 1, 0.4, 1)
        else:
            self.status.text = "Ошибка"
            self.status.color = (1, 0.4, 0.4, 1)
        self.btn_proc.disabled = False
        self.btn_sel.disabled = False

if __name__ == "__main__":
    ClipperApp().run()
