FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    DATEN_ORDNER=/daten

WORKDIR /app
COPY requirements.txt requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-web.txt
COPY rauchmelder_platzierung.py webapp.py ./

# nicht als root laufen; Auftraege liegen im Volume /daten
RUN useradd --system --create-home --uid 10001 app && mkdir /daten && chown app /daten
USER app
VOLUME /daten
EXPOSE 8000

# Zeitlimit ueber der Rechenzeit (RECHENZEIT_S, Standard 1800 s)
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "1900", "webapp:app"]
