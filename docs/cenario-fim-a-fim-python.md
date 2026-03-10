# Cenário fim a fim: Webhook -> EDA -> ação Python local

Este documento descreve o cenário em que um evento recebido por webhook é processado no EDA e aciona um script Python local via `run_module`.

O fluxo validado aqui é:

`Evento HTTP` → `Event Stream` → `Activation EDA` → `Rulebook` → `run_module` → `python_action_demo.py`

## Objetivo do cenário

Este cenário prova que o laboratório consegue:

1. receber um evento JSON via webhook;
2. entregar o evento para um rulebook no EDA;
3. avaliar a severidade do payload;
4. executar um script Python local quando a condição for satisfeita;
5. enriquecer o evento e, opcionalmente, gravar uma trilha em arquivo.

## Componentes envolvidos

### EDA

- `Event Stream`: `lab-webhook`
- `Project`: `testes-eda-project`
- `Rulebook`: `python_demo.yml`
- `Activation`: `activation-python-demo`

### Arquivos usados no cenário

- `rulebooks/python_demo.yml`
  - rulebook que chama a action Python.
- `scripts/python_action_demo.py`
  - script local executado pelo EDA.
- `scripts/create_eda_hello_webhook_stack.py`
  - cria o lado EDA do cenário.
- `scripts/send_webhook_events.py`
  - envia o evento de teste.

## Como o fluxo funciona

## Forma suportada neste ambiente

No runtime atual do EDA usado neste laboratório, a forma suportada para o cenário Python é:

- `run_module`
- `name: ansible.builtin.command`
- `hosts: localhost` no ruleset
- comando chamando `python3 scripts/python_action_demo.py`

A tentativa com `run_script` não funcionou neste ambiente e gerou o erro:

```text
Action run_script not supported
```

Por isso, este documento considera `run_module` como a forma correta e suportada para esse fluxo.

### Etapa 1. Recebimento do evento

Um payload JSON entra no webhook do `Event Stream`.

### Etapa 2. Entrega ao rulebook

O EDA entrega o evento ao source `lab_webhook` do rulebook `python_demo.yml`.

### Etapa 3. Avaliação da condição

O rulebook executa a action Python quando:

```yaml
event.payload.payload.severity == "high"
```

### Etapa 4. Execução do script local

O EDA executa `ansible.builtin.command` localmente e chama `scripts/python_action_demo.py` com dados do evento, incluindo:

- `event_id`
- `host`
- `severity`
- `message`
- `sent_at`
- `source`
- `event_type`
- `sequence`

### Etapa 5. Enriquecimento do evento

O script:

- normaliza os campos recebidos;
- classifica a severidade em prioridade (`p1`, `p2`, `p3`);
- gera um `summary`;
- calcula um fingerprint SHA-256;
- opcionalmente grava uma linha JSON em arquivo se `EDA_PYTHON_ACTION_LOG` ou `--output-file` estiver definido.

## Trechos principais

### Rulebook do EDA

Arquivo: `rulebooks/python_demo.yml`

```yaml
---
- name: Webhook -> Python (EDA local action demo)
  hosts: localhost

  sources:
    - name: lab_webhook
      ansible.eda.pg_listener:
        channels:
          - lab_webhook

  rules:
    - name: Quando severity for high, roda um Python
      condition: event.payload.payload.severity == "high"
      actions:
        - print_event:
            pretty: true
        - run_module:
            name: ansible.builtin.command
            module_args:
              cmd: >-
                python3 scripts/python_action_demo.py
                --event-id "{{ event.payload.id | default('') }}"
                --host "{{ event.payload.payload.host | default('') }}"
                --severity "{{ event.payload.payload.severity | default('') }}"
                --message "{{ event.payload.payload.message | default('') }}"
                --sent-at "{{ event.payload.sent_at | default('') }}"
                --source "{{ event.payload.source | default('') }}"
                --event-type "{{ event.payload.type | default('') }}"
                --sequence "{{ event.payload.sequence | default('') }}"
```

### Script Python

Arquivo: `scripts/python_action_demo.py`

O script imprime um JSON enriquecido e pode gravar o mesmo conteúdo em JSONL para auditoria local.

## Passo a passo reprodutível

### 1. Criar Project + Activation no EDA

```bash
export EDA_BASE_URL='https://sandbox-aap-danielamaral789-dev.apps.rm1.0a51.p1.openshiftapps.com'
python3 scripts/create_eda_hello_webhook_stack.py \
  --base-url "$EDA_BASE_URL" \
  --project-name 'testes-eda-project' \
  --project-url 'https://github.com/danielamaral789/testes_eda.git' \
  --project-branch 'main' \
  --rulebook 'python_demo.yml' \
  --activation 'activation-python-demo' \
  --decision-environment 'de-hello-webhook' \
  --de-image 'quay.io/ansible/ansible-rulebook:v1.2.1' \
  --event-stream 'lab-webhook'
```

### 2. Enviar um evento de teste

```json
{
  "id": "python-demo-1",
  "sent_at": "2026-03-08T00:00:00Z",
  "sequence": 101,
  "source": "testes_eda",
  "type": "synthetic",
  "payload": {
    "severity": "high",
    "host": "localhost",
    "message": "Python demo action"
  }
}
```

```bash
export EDA_WEBHOOK_URL='https://<...>/eda-event-streams/api/eda/v1/external_event_stream/<uuid>/post/'
TOKEN="$(cat local/lab-webhook.token)"

python3 scripts/send_webhook_events.py \
  --url "$EDA_WEBHOOK_URL" \
  --header "Authorization: $TOKEN" \
  --count 1 \
  --data '<JSON_ACIMA>'
```

## Como validar

### Validação no EDA

- abrir os logs da `activation-python-demo`;
- verificar o `print_event`;
- verificar a saída JSON do `python_action_demo.py`.

### Resultado esperado do script

O JSON de saída deve incluir campos como:

- `priority`
- `summary`
- `fingerprint_sha256`
- `source`
- `event_type`
- `sequence`

Se houver arquivo configurado para auditoria, a saída também informa `written_to`.

## Relação com o restante do projeto

- o `README.md` explica o laboratório como um todo;
- este documento cobre especificamente o cenário `EDA -> Python local`.
