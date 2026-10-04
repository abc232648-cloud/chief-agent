FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium
COPY gateway ./gateway
COPY policy ./policy
COPY agents ./agents
COPY application ./application
COPY capabilities ./capabilities
COPY compatibility ./compatibility
COPY update_center ./update_center
COPY runtime_qualification ./runtime_qualification
COPY domains ./domains
COPY security ./security
COPY control ./control
COPY skills ./skills
COPY candidate ./candidate
COPY worker ./worker
COPY browser ./browser
COPY database ./database
COPY operations ./operations
COPY evidence ./evidence
COPY data_quality ./data_quality
COPY decision_ledger ./decision_ledger
COPY model_registry ./model_registry
COPY runbooks ./runbooks
COPY identity ./identity
COPY config ./config
COPY notifications ./notifications
COPY scheduler.py ./scheduler.py
COPY monitoring.py ./monitoring.py
COPY monitor_runner.py ./monitor_runner.py
COPY dashboard_app.py ./dashboard_app.py
COPY dashboard.html ./dashboard.html
COPY static ./static
COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x ./docker-entrypoint.sh
EXPOSE 8765
CMD ["./docker-entrypoint.sh"]
