import time
import asyncio

def bar(p: float, w=20):
    filled = int(p*w)
    return "█"*filled + "░"*(w-filled)

class Tracker:
    def __init__(self, client, chat_id, msg_id, interval=2.0):
        self.client = client
        self.chat_id = chat_id
        self.msg_id = msg_id
        self.interval = interval
        self.last = 0
        self.start = time.time()
        self.spin = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
        self.idx = 0

    def _sp(self):
        c = self.spin[self.idx % len(self.spin)]
        self.idx+=1
        return c

    async def update(self, pct: float, stage: str, speed=0, eta=0, force=False):
        now = time.time()
        if not force and now-self.last < self.interval:
            return
        self.last = now
        b = bar(pct)
        elapsed = int(now-self.start)
        sp = self._sp()
        txt = f"{sp} **{stage}**\n`[{b}]` {pct*100:.1f}%\n"
        if speed or eta:
            txt += f"⚡ {speed} | ⏱️ {elapsed}s | ⏳ {eta}\n"
        try:
            await self.client.edit_message_text(self.chat_id, self.msg_id, txt)
        except:
            pass

    async def edit(self, txt: str):
        try:
            await self.client.edit_message_text(self.chat_id, self.msg_id, txt)
        except:
            pass
