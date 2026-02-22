FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
```

**4.** Clique **"Commit new file"**

---

**5.** Ensuite va modifier le **`Procfile`** → clique dessus → ✏️ crayon → remplace le contenu par :
```
worker: python main.py
