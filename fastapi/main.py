from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {"message": "Bot API funcionando 🚀"}

@app.get("/health")
def health():
    return {"status": "ok"}
