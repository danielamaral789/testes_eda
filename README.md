# testes_eda

Laboratório local para testar **Event-Driven Ansible (EDA)** com foco em **webhook**.

## O que tem aqui

- `scripts/send_webhook_events.py`: gerador local (stdlib) que envia eventos JSON para uma URL de webhook (ex.: *Event Stream* do EDA).
- `events/sample_event.template.json`: template de exemplo com placeholders.
- `scripts/create_eda_event_stream.py`: cria (via API) um **Event Stream** no EDA com credencial **Token Event Stream** para você já ter uma URL de webhook funcional.
- `scripts/create_eda_hello_webhook_stack.py`: cria (via API) um stack mínimo **Project + Decision Environment + Activation** usando o `demo_webhook_rulebook.yml`.
- `scripts/cleanup_eda_hello_webhook_stack.py`: remove do EDA os recursos do stack do lab (útil para “zerar” e testar do início).
- `scripts/load_test_webhook.py`: load test do webhook + geração de relatório HTML.
- `rulebooks/jobtemplate_demo.yml`: exemplo de rulebook que recebe eventos do `lab-webhook` e dispara um **Job Template** do Controller (AAP) quando `severity=high`.

## Fluxo recomendado (EDA)

1. No EDA UI, crie um **Event Stream** (webhook) e copie a **Webhook URL** gerada.
2. Crie/edite uma **Rulebook Activation** e selecione esse Event Stream em *Event streams*.
3. Rode o gerador local apontando para a URL do webhook.

> Importante: não versionar segredos. Prefira usar variáveis de ambiente.

## Estado do lab (criado até agora)

Criado em `2026-03-08` no EDA:

- **Event Stream**: `lab-webhook` (tipo `token`)  
  Serve para expor uma URL de webhook que aceita eventos HTTP e alimenta o EDA.
- **EDA Credential**: `lab-webhook-token` (Credential Type: `Token Event Stream`)  
  Serve para definir como o webhook valida autenticação (token em header).
- **Project**: `hello-webhook-project` (Git)  
  Serve para trazer rulebooks via Git. Foi apontado para o repositório público `ansible/event-driven-ansible`.
- **Rulebook**: `demo_webhook_rulebook.yml` (dentro do Project)  
  Serve como “hello world” do webhook: ele faz `print_event` de tudo que chega.
- **Decision Environment**: `de-hello-webhook`  
  Container usado para executar o `ansible-rulebook` (imagem atual: `quay.io/ansible/ansible-rulebook:v1.2.1`).
- **Activation**: `activation-hello-webhook`  
  Roda o rulebook e mapeia o Event Stream `lab-webhook` para o source do rulebook (source `__SOURCE_1`).

Próximos passos (para evoluir além do “hello world”):
- Criar um rulebook próprio do lab (em um repo Git seu) e trocar o rulebook da Activation.
- Criar regras com condições reais e ações (ex.: chamar Controller, abrir ticket, etc.).

## Enviar um evento (teste rápido)

Defina a URL do webhook (copiada do EDA):

```bash
export EDA_WEBHOOK_URL='https://...'
python3 scripts/send_webhook_events.py --count 1
```

Se o seu Event Stream for do tipo **token** (como o `lab-webhook`), envie também o header:

```bash
export EDA_WEBHOOK_URL='https://...'
python3 scripts/send_webhook_events.py --header 'Authorization: <TOKEN>' --count 1
```

## Ver o evento chegando no EDA

Como o rulebook `demo_webhook_rulebook.yml` só faz `print_event`, o jeito mais simples de ver o evento é pelos **logs da Activation**:

- UI: **Rulebook Activations** → `activation-hello-webhook` → **History** → abrir a instância → **Logs**
- API: `GET /api/eda/v1/activation-instances/<id>/logs/`

## Load test (dar porrada no webhook) + relatório de degradação

Script: `scripts/load_test_webhook.py`

Ele envia eventos em volume para a URL do webhook, mede latência por request e gera:
- `reports/<run>.samples.jsonl` (amostras)
- `reports/<run>.summary.json` (resumo)
- `reports/<run>.report.html` (página com gráficos)

O relatório marca o primeiro instante em que o **p95** (janela móvel) ultrapassa um threshold calculado a partir de um **baseline** (primeiros N segundos).

Exemplo (com Event Stream tipo token):

```bash
export EDA_WEBHOOK_URL='https://...'
python3 scripts/load_test_webhook.py \
  --token-file local/lab-webhook.token \
  --duration 120 \
  --concurrency 20 \
  --rate 50 \
  --vary
```

Dicas rápidas de parâmetros:
- `--rate 0` tenta “saturar” (sem limite); normalmente prefira setar um RPS.
- `--baseline-seconds`, `--window-seconds`, `--increase-factor`, `--consecutive` controlam a detecção do “começou a piorar aqui”.
- Para achar o “ponto de virada”, prefira um **ramp test** (a carga sobe ao longo do tempo), ex.:

```bash
python3 scripts/load_test_webhook.py \
  --url "$EDA_WEBHOOK_URL" \
  --header 'Authorization: <TOKEN>' \
  --duration 180 \
  --warmup 5 \
  --concurrency 50 \
  --ramp-start 5 \
  --ramp-end 150 \
  --vary
```

### Boas práticas (relatório em página web)

Para ficar fácil de ler (e comparar runs), um bom relatório de teste de API costuma ter:
- **Cards no topo**: URL, duração, total reqs, erros, RPS médio, p50/p95/p99.
- **Séries temporais**: p50+p95 por segundo e RPS+erros por segundo.
- **Marcação de eventos**: linha vertical no “ponto de degradação” e anotar baseline/threshold.
- **Definição explícita**: o que é “degradação” (ex.: “p95 da janela de 10s > 1.5x baseline por 3 janelas”).
- **Artefatos brutos**: salvar amostras (JSONL) para auditoria/reprocessamento.

### Troubleshooting rápido (403/503)

- `403 Forbidden` quase sempre significa **token ausente/errado** (header não enviado, variável vazia, ou você rotacionou o token e está usando o antigo).
- `503` significa que o endpoint ficou **sem capacidade** (saturou ou houve indisponibilidade no upstream).

## Exemplo: EDA chamando Job Template do Controller (AAP)

Rulebook pronto neste repo: `rulebooks/jobtemplate_demo.yml`.

O que ele faz:
- Recebe eventos do Event Stream `lab-webhook` (via source `lab_webhook`).
- Se `event.payload.payload.severity == "high"`, chama o Job Template `Demo - Remediate Host` no org `Default`, passando `extra_vars` (host/severity/message/event_id).

Pontos importantes no AAP/EDA:
- No EDA, crie uma **Credential** do tipo **Red Hat Ansible Automation Platform** (Controller URL + credenciais/token) e associe essa credencial à Activation que vai rodar esse rulebook.
- No Controller, o Job Template precisa aceitar variáveis na execução (ex.: **Prompt on launch** para *Variables*), senão os `extra_vars` não entram.

### Últimos runs (após reset em 2026-03-08)

- Run “alto” (rampa `10→400`, `--concurrency 100`): `reports/loadtest-20260308-140322.report.html`  
  Predominou `HTTP 503` e `TimeoutError` (saturou praticamente de cara).
- Run “mais controlado” (rampa `1→80`, `--concurrency 10`): `reports/loadtest-20260308-141844.report.html`  
  `success_rate ~98.75%` e o detector marcou início de degradação por volta do **segundo 16** (p95 ~0.53s → ~1.08s).

## Criar Event Stream via script (sem colocar senha no comando)

Se você quiser automatizar a criação do **Event Stream** sem expor a senha no histórico do shell, use:

```bash
export EDA_BASE_URL='https://sandbox-aap-danielamaral789-dev.apps.rm1.0a51.p1.openshiftapps.com'
python3 scripts/create_eda_event_stream.py --name lab-webhook
```

Ele vai pedir a senha interativamente (ou use `EDA_PASSWORD` via ambiente, se preferir).

Se você precisar **rotacionar o token** (recomendado se ele foi exposto em chat/log):

```bash
export EDA_BASE_URL='https://sandbox-aap-danielamaral789-dev.apps.rm1.0a51.p1.openshiftapps.com'
python3 scripts/create_eda_event_stream.py --name lab-webhook --rotate-token
```

O token é secreto e fica armazenado **criptografado** no EDA; guarde o token do output em um `.env` local (ignorando git) para usar no `send_webhook_events.py`.

## Criar o stack “hello webhook” via script

Cria/ajusta: Project + Decision Environment + Activation (assumindo que o Event Stream `lab-webhook` já existe):

```bash
export EDA_BASE_URL='https://sandbox-aap-danielamaral789-dev.apps.rm1.0a51.p1.openshiftapps.com'
python3 scripts/create_eda_hello_webhook_stack.py --base-url "$EDA_BASE_URL"
```

## Reset do lab (limpar tudo e recomeçar)

1) Apaga os recursos no EDA (Activation/Project/DE/Event Stream e, opcionalmente, as creds do token):

```bash
export EDA_BASE_URL='https://sandbox-aap-danielamaral789-dev.apps.rm1.0a51.p1.openshiftapps.com'
python3 scripts/cleanup_eda_hello_webhook_stack.py --base-url "$EDA_BASE_URL" --delete-token-creds
```

2) Recria o Event Stream e grava o token localmente (arquivos ignorados pelo git):

```bash
python3 scripts/create_eda_event_stream.py \
  --base-url "$EDA_BASE_URL" \
  --name lab-webhook \
  --rotate-token \
  --write-token local/lab-webhook.token \
  > local/lab-webhook.event_stream.json
```

3) Recria o stack “hello webhook”:

```bash
python3 scripts/create_eda_hello_webhook_stack.py --base-url "$EDA_BASE_URL" > local/hello-webhook.stack.json
```

## Contrato real da API (observado neste lab)

Esta seção documenta o que a API **realmente exigiu** aqui no ambiente (campos obrigatórios, `source_mappings` e o esquema de autenticação por sessão/CSRF).

### Autenticação: sessão + CSRF (Gateway)

- Login é feito via **AAP Gateway**:
  - `GET /api/gateway/v1/login/` para receber o cookie `csrftoken`
  - `POST /api/gateway/v1/login/` com `username`/`password` (form-encoded) e header `X-Csrftoken: <cookie csrftoken>`
- Depois do login, as chamadas para `/api/eda/v1/...` funcionam com:
  - cookies de sessão retornados pelo login
  - header `X-CSRFToken: <csrftoken>` (note o nome do header: `X-CSRFToken`)
  - e, no nosso caso, também ajudou enviar `Origin: <base_url>` e `Referer: <base_url>/`

Atalho para “descobrir o contrato”:
- `OPTIONS /api/eda/v1/<recurso>/` (retorna schema com campos/required)

### Event Streams

Endpoints usados:
- listar: `GET /api/eda/v1/event-streams/?test_mode=false&page=1&page_size=200`
- schema: `OPTIONS /api/eda/v1/event-streams/`
- criar: `POST /api/eda/v1/event-streams/`

Campos que o server exigiu no `POST`:
- `name` (string)
- `organization_id` (int)
- `eda_credential_id` (int)
- `test_mode` (bool, opcional)

Obs:
- O token fica armazenado como **criptografado** (aparece como `"$encrypted$"` ao listar o Event Stream / credencial). Ou seja: se você não guardou o token quando criou, a API não te devolve em claro.

### EDA Credentials (para Event Stream tipo token)

Endpoints usados:
- schema: `OPTIONS /api/eda/v1/eda-credentials/`
- criar: `POST /api/eda/v1/eda-credentials/`

Para credencial **Token Event Stream** (credential_type_id = `8` neste ambiente), o payload que funcionou foi:
- `name` (string)
- `organization_id` (int)
- `credential_type_id` (int)
- `inputs` (objeto), contendo:
  - `auth_type: "token"`
  - `token: "<TOKEN>"`
  - `http_header_key: "Authorization"` (ou outro header)

### Projects (Git)

Endpoints usados:
- schema: `OPTIONS /api/eda/v1/projects/`
- criar: `POST /api/eda/v1/projects/`
- status/import: `GET /api/eda/v1/projects/<id>/`

Campos que o server exigiu no `POST`:
- `name` (string)
- `organization_id` (int)
- `url` (string) — Git URL

Campos que usamos (práticos):
- `scm_type: "git"`
- `scm_branch: "main"`
- `verify_ssl: true`

Notas:
- A importação do repo aparece em `import_state` (`pending` → `completed`/`successful` ou `failed`/`error`)
- Os rulebooks do projeto aparecem em `GET /api/eda/v1/rulebooks/?project_id=<id>`

### Decision Environments

Endpoints usados:
- schema: `OPTIONS /api/eda/v1/decision-environments/`
- criar: `POST /api/eda/v1/decision-environments/`

Campos que o server exigiu no `POST`:
- `name` (string)
- `organization_id` (int)
- `image_url` (string)

Campos úteis:
- `pull_policy` (`missing`, `always`, `never`)

### Rulebook Activation (o ponto “chato”: source_mappings)

Endpoints usados:
- schema: `OPTIONS /api/eda/v1/activations/`
- criar: `POST /api/eda/v1/activations/`

Campos que o server exigiu no `POST`:
- `name` (string)
- `organization_id` (int)
- `decision_environment_id` (int)
- `rulebook_id` (int)

Campos que usamos:
- `is_enabled` (bool)
- `restart_policy` (`always`/`on-failure`/`never`)
- `log_level` (`debug`/`info`/`error`)
- `source_mappings` (string YAML) — necessário para plugar Event Stream em rulebook

`source_mappings` precisa conter **todas** essas chaves por mapping (foi o que a API validou aqui):
- `source_name` (ex.: `__SOURCE_1`)
- `event_stream_id` (string com id numérico)
- `event_stream_name` (nome do Event Stream)
- `rulebook_hash` (hash retornado pelo endpoint de sources do rulebook)

Como descobrir `source_name` e `rulebook_hash`:
- `GET /api/eda/v1/rulebooks/<rulebook_id>/sources/?page=1&page_size=200`

Como descobrir `event_stream_id` e confirmar `event_stream_name`:
- `GET /api/eda/v1/event-streams/?test_mode=false&page=1&page_size=200`

Exemplo (YAML string) que funcionou no lab:

```yaml
- source_name: __SOURCE_1
  event_stream_id: '1'
  event_stream_name: lab-webhook
  rulebook_hash: <RULEBOOK_HASH>
```

### Logs e “ver eventos”

O jeito mais simples de ver o payload entrando (com o rulebook demo que faz `print_event`) é via logs da instância:
- listar instâncias: `GET /api/eda/v1/activation-instances/?activation_id=<id>&page_size=20`
- logs: `GET /api/eda/v1/activation-instances/<instance_id>/logs/?page_size=200`

Usando um template:

```bash
export EDA_WEBHOOK_URL='https://...'
python3 scripts/send_webhook_events.py --template events/sample_event.template.json --count 5 --interval 1
```

Headers extras (se o seu webhook exigir):

```bash
python3 scripts/send_webhook_events.py \
  --url 'https://...' \
  --header 'X-My-Header: 123' \
  --count 1
```

HMAC opcional (apenas se o seu endpoint validar assinatura):

```bash
export EDA_WEBHOOK_URL='https://...'
export EDA_WEBHOOK_HMAC_SECRET='...'
export EDA_WEBHOOK_HMAC_HEADER='X-Hub-Signature-256'
export EDA_WEBHOOK_HMAC_PREFIX='sha256='
python3 scripts/send_webhook_events.py --count 1
```
