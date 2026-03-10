# testes_eda

Laboratório para testar **Event-Driven Ansible (EDA)** com foco em **webhooks**, **rulebooks**, **automação orientada a eventos** e integração com **Automation Controller (AAP)**.

## Descrição

Este repositório não é uma aplicação de produção. Ele funciona como um **kit de laboratório e automação reprodutível** para:

- provisionar componentes do EDA via API;
- criar e testar um webhook de entrada baseado em **Event Stream**;
- enviar eventos sintéticos para validar regras e ações;
- executar ações locais no EDA ou disparar **Job Templates** no Controller;
- medir capacidade, latência e degradação do endpoint sob carga.

Em termos práticos, o fluxo principal é:

`Evento HTTP` → `Event Stream` → `Activation EDA` → `Rulebook` → `Ação local ou Job Template no Controller`

## O que fazemos aqui

O workspace cobre quatro frentes principais:

1. **Provisionamento do lab**
   - cria `Event Stream`, `Decision Environment`, `Project`, `Activation` e credenciais no EDA;
   - cria `Project`, `Inventory`, `Host` e `Job Template` no Controller.

2. **Geração e envio de eventos**
   - envia eventos JSON sintéticos para o webhook;
   - permite usar template, payload inline, headers extras e HMAC.

3. **Execução das automações**
   - processa eventos em rulebooks;
   - executa `run_script` local ou `run_job_template` no Controller.

4. **Operação e troubleshooting**
   - reinicia activation, sincroniza projeto, valida jobs, diagnostica webhook;
   - executa testes de carga e gera relatórios HTML.

## Mapa do projeto

### Documentação

- `README.md`
  - visão geral do laboratório, estrutura, fluxo e comandos principais.
- `docs/teste-fim-a-fim-eda-controller-jobtemplate.md`
  - roteiro do teste completo `webhook -> EDA -> Controller`, com validação e troubleshooting.

### Rulebooks

- `rulebooks/jobtemplate_demo.yml`
  - recebe eventos do canal `lab_webhook` e chama o Job Template `Demo - Remediate Host`.
- `rulebooks/python_demo.yml`
  - recebe eventos do mesmo canal e executa `scripts/python_action_demo.py`.
- `jobtemplate_demo.yml`
  - cópia do rulebook de Job Template na raiz.
- `python_demo.yml`
  - cópia do rulebook Python na raiz.

### Playbooks e inventário

- `playbooks/remediate_host.yml`
  - playbook demo do Controller; exibe as variáveis recebidas do evento.
- `inventories/localhost.ini`
  - inventário mínimo com `localhost` usando conexão local.

### Eventos

- `events/sample_event.template.json`
  - template JSON com placeholders `${uuid}`, `${now}` e `${sequence}`.

### Scripts de provisionamento EDA

- `scripts/create_eda_event_stream.py`
  - cria ou rotaciona `Event Stream`, token e URL do webhook.
- `scripts/create_eda_hello_webhook_stack.py`
  - cria/atualiza `Decision Environment`, `Project`, `Rulebook` e `Activation`.
- `scripts/create_eda_aap_controller_credential.py`
  - cria credencial AAP no EDA para habilitar ações como `run_job_template`.
- `scripts/cleanup_eda_hello_webhook_stack.py`
  - remove recursos do lab no EDA.
- `scripts/sync_eda_project.py`
  - força sincronização/import de projeto EDA.
- `scripts/toggle_eda_activation.py`
  - reinicia uma activation via disable/enable.

### Scripts de provisionamento Controller

- `scripts/create_controller_job_template_demo.py`
  - cria `Project`, `Inventory`, host `localhost` e `Job Template` no Controller.
- `scripts/check_controller_latest_job.py`
  - consulta o último job de um `job_template_id` e aguarda finalização.

### Scripts de geração e teste de eventos

- `scripts/send_webhook_events.py`
  - envia eventos sintéticos ao webhook com suporte a template, headers e HMAC.
- `scripts/check_webhook_endpoint.py`
  - faz um smoke test do endpoint com diagnóstico rápido.
- `scripts/load_test_webhook.py`
  - executa teste de carga e gera artefatos e relatório HTML.

### Scripts auxiliares

- `scripts/introspect_eda_schema.py`
  - inspeciona schemas reais da API EDA via `OPTIONS`.
- `scripts/get_eda_credential_type.py`
  - consulta um tipo de credencial EDA por id.
- `scripts/python_action_demo.py`
  - ação Python simples acionada pelo rulebook `python_demo`.

## Árvore visual do projeto

```text
testes_eda/
├── README.md
│   └── Guia principal do laboratório e do fluxo operacional.
├── docs/
│   └── teste-fim-a-fim-eda-controller-jobtemplate.md
│       └── Passo a passo do cenário EDA -> Controller.
├── events/
│   └── sample_event.template.json
│       └── Template de payload para geração de eventos.
├── inventories/
│   └── localhost.ini
│       └── Inventário local usado no demo do Controller.
├── playbooks/
│   └── remediate_host.yml
│       └── Playbook demo que recebe variáveis do evento.
├── rulebooks/
│   ├── jobtemplate_demo.yml
│   │   └── Rulebook que chama Job Template no Controller.
│   └── python_demo.yml
│       └── Rulebook que executa script Python local.
├── jobtemplate_demo.yml
│   └── Cópia do rulebook de Job Template na raiz.
├── python_demo.yml
│   └── Cópia do rulebook Python na raiz.
└── scripts/
    ├── check_controller_latest_job.py
    │   └── Consulta e acompanha o último job do Controller.
    ├── check_webhook_endpoint.py
    │   └── Testa rapidamente o webhook.
    ├── cleanup_eda_hello_webhook_stack.py
    │   └── Limpa recursos do laboratório no EDA.
    ├── create_controller_job_template_demo.py
    │   └── Provisiona o lado Controller do demo.
    ├── create_eda_aap_controller_credential.py
    │   └── Cria a credencial AAP no EDA.
    ├── create_eda_event_stream.py
    │   └── Cria o Event Stream e gerencia token.
    ├── create_eda_hello_webhook_stack.py
    │   └── Provisiona o lado EDA do demo.
    ├── get_eda_credential_type.py
    │   └── Consulta tipos de credencial do EDA.
    ├── introspect_eda_schema.py
    │   └── Descobre schemas reais da API EDA.
    ├── load_test_webhook.py
    │   └── Executa carga no webhook e gera relatório.
    ├── python_action_demo.py
    │   └── Script local acionado por rulebook.
    ├── send_webhook_events.py
    │   └── Envia eventos sintéticos para o webhook.
    ├── sync_eda_project.py
    │   └── Força sync/import de projeto EDA.
    └── toggle_eda_activation.py
        └── Reinicia uma activation do EDA.
```

## Fluxo operacional

### 1. Provisionar o webhook no EDA

Use `scripts/create_eda_event_stream.py` para:

- criar o `Event Stream`;
- criar ou rotacionar o token;
- descobrir a URL do webhook;
- opcionalmente gravar o token em arquivo local.

### 2. Provisionar a execução no EDA

Use `scripts/create_eda_hello_webhook_stack.py` para:

- criar ou atualizar `Decision Environment`;
- criar ou atualizar `Project`;
- aguardar import do repositório Git;
- localizar o `Rulebook`;
- montar o `source_mappings`;
- criar ou atualizar a `Activation`.

### 3. Escolher o tipo de automação

Há dois fluxos prontos:

- `rulebooks/python_demo.yml`
  - quando `severity == "high"`, executa `scripts/python_action_demo.py`;
- `rulebooks/jobtemplate_demo.yml`
  - quando `severity == "high"`, chama o Job Template `Demo - Remediate Host` no Controller.

### 4. Se usar Controller, preparar o destino

Antes de usar `run_job_template`, é preciso:

1. criar o lado Controller com `scripts/create_controller_job_template_demo.py`;
2. criar a credencial AAP no EDA com `scripts/create_eda_aap_controller_credential.py`;
3. anexar essa credencial à `Activation`.

### 5. Enviar eventos

Use:

- `scripts/send_webhook_events.py` para enviar eventos sintéticos;
- `scripts/check_webhook_endpoint.py` para um teste rápido do endpoint.

### 6. Processamento no EDA

O ciclo interno é:

1. o webhook recebe um JSON;
2. o `Event Stream` entrega o evento ao EDA;
3. a `Activation` envia o evento ao source do rulebook;
4. a condição da regra avalia o conteúdo do payload;
5. a ação configurada é executada.

### 7. Executar a ação

- No fluxo Python:
  - o EDA executa `scripts/python_action_demo.py`.

- No fluxo Controller:
  - o EDA chama o Job Template no Controller;
  - o Controller executa `playbooks/remediate_host.yml`.

### 8. Validar o resultado

Use:

- logs da `Activation` no EDA para validar entrada e execução;
- `scripts/check_controller_latest_job.py` para confirmar execução do Job Template no Controller.

### 9. Medir capacidade e degradação

Use `scripts/load_test_webhook.py` para:

- aplicar carga constante ou rampa;
- medir latência e taxa de erro;
- detectar degradação por `baseline p95` e janela móvel;
- gerar artefatos `.jsonl`, `.json` e `.html`.

### 10. Operar e reciclar o lab

Use:

- `scripts/sync_eda_project.py` para reimportar projeto;
- `scripts/toggle_eda_activation.py` para reiniciar activation;
- `scripts/cleanup_eda_hello_webhook_stack.py` para limpar o ambiente e recomeçar.

## Fluxo fim a fim em uma linha

`send_webhook_events.py` → `Event Stream` → `Activation` → `Rulebook` → `run_script` ou `run_job_template` → `logs / job do Controller`

## Comandos mais úteis

### Criar Event Stream

```bash
export EDA_BASE_URL='https://...'
python3 scripts/create_eda_event_stream.py --base-url "$EDA_BASE_URL"
```

### Criar stack no EDA

```bash
export EDA_BASE_URL='https://...'
python3 scripts/create_eda_hello_webhook_stack.py --base-url "$EDA_BASE_URL"
```

### Enviar um evento

```bash
export EDA_WEBHOOK_URL='https://...'
python3 scripts/send_webhook_events.py --count 1
```

### Enviar com token

```bash
export EDA_WEBHOOK_URL='https://...'
python3 scripts/send_webhook_events.py --header 'Authorization: <TOKEN>' --count 1
```

### Testar carga

```bash
export EDA_WEBHOOK_URL='https://...'
python3 scripts/load_test_webhook.py \
  --token-file local/lab-webhook.token \
  --duration 120 \
  --concurrency 20 \
  --rate 50 \
  --vary
```

## Notas que ainda fazem sentido manter

### Autenticação da API

Os scripts do lab usam autenticação via **AAP Gateway + sessão + CSRF**:

- `GET /api/gateway/v1/login/` para obter `csrftoken`;
- `POST /api/gateway/v1/login/` para abrir sessão;
- chamadas seguintes para `/api/eda/v1/...` e `/api/controller/v2/...` usam cookies + `X-CSRFToken`.

### Descoberta de schema real

Quando houver dúvida sobre payloads aceitos pela API, use:

```bash
python3 scripts/introspect_eda_schema.py --base-url 'https://...'
```

### Segredos

- não versionar tokens, senhas ou credenciais;
- prefira variáveis de ambiente ou arquivos locais ignorados pelo Git;
- se um token for exposto, rotacione com `scripts/create_eda_event_stream.py --rotate-token`.
