# Visheshak on Hugging Face Spaces (Docker SDK).
# Runs the trading agent loop in the background and the dashboard in the
# foreground on port 7860 (the Space's expected app port).
FROM python:3.11-slim

# Spaces run containers as uid 1000 with a writable /home/user only.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user . .

ENV HOST=0.0.0.0 PORT=7860
EXPOSE 7860
CMD ["bash", "start.sh"]
