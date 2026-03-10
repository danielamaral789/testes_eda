# Cenário fim a fim: Webhook -> EDA -> Job Template no Controller

Este documento descreve o cenário completo de integração entre:

- um **webhook** exposto por um `Event Stream`;
- um **rulebook** executado por uma `Activation` no EDA;
- um **Job Template** executado no **Automation Controller (AAP)**.

O objetivo é validar o fluxo:

`Evento HTTP` → `Event Stream` → `Activation EDA` → `Rulebook` → `run_job_template` → `Controller Job`

## Objetivo do cenário

Este cenário prova que o laboratório consegue:

1. receber um evento JSON via webhook;
2. entregar o evento ao EDA por meio de um `Event Stream`;
3. avaliar o evento em um rulebook;
4. disparar um `Job Template` no Controller;
5. passar dados do evento para o playbook via `extra_vars`.

## Componentes envolvidos

### EDA

- `Event Stream`: `lab-webhook`
  - endpoint HTTP que recebe o evento de entrada.
- `EDA Credential (AAP)`: `aap-local-controller`
  - credencial usada pelo EDA para se autenticar no Controller.
- `Project`: `testes-eda-project`
  - projeto Git usado pelo EDA para importar os rulebooks deste repositório.
- `Rulebook`: `jobtemplate_demo.yml`
  - regra que decide quando chamar o Controller.
- `Activation`: `activation-jobtemplate-demo`
  - processo que executa o rulebook e vincula o `Event Stream` ao source do rulebook.

### Controller

- `Project`: `controller-testes-eda-project`
  - projeto Git usado pelo Controller para importar os playbooks deste repositório.
- `Inventory`: `eda-demo-inventory`
  - inventário do demo.
- `Host`: `localhost`
  - host local usado para execução do playbook.
- `Job Template`: `Demo - Remediate Host`
  - job disparado pelo EDA.

### Arquivos do repositório usados no cenário

- `rulebooks/jobtemplate_demo.yml`
  - rulebook que escuta o evento e chama o Controller.
- `playbooks/remediate_host.yml`
  - playbook executado pelo Job Template.
- `scripts/create_controller_job_template_demo.py`
  - provisiona o lado Controller.
- `scripts/create_eda_aap_controller_credential.py`
  - cria a credencial AAP no EDA.
- `scripts/create_eda_hello_webhook_stack.py`
  - provisiona o lado EDA.
- `scripts/send_webhook_events.py`
  - envia o evento de teste.
- `scripts/check_controller_latest_job.py`
  - valida o último job disparado no Controller.

## Como o fluxo funciona

### Etapa 1. Recebimento do evento

Um payload JSON é enviado para a URL do webhook do `Event Stream` `lab-webhook`.

### Etapa 2. Entrada no EDA

O `Event Stream` entrega o evento para a `Activation`, que o repassa ao source `lab_webhook` definido no rulebook.

### Etapa 3. Avaliação da regra

O rulebook avalia a condição:

```yaml
event.payload.payload.severity == "high"
```

Se a condição for verdadeira, a action `run_job_template` é executada.

### Etapa 4. Chamada ao Controller

O EDA usa a credencial AAP anexada à `Activation` para autenticar no Controller e disparar o Job Template `Demo - Remediate Host`.

### Etapa 5. Execução do playbook

O Controller executa o playbook `playbooks/remediate_host.yml`, recebendo informações do evento em `extra_vars`.

## Trechos principais

### Rulebook do EDA

Arquivo: `rulebooks/jobtemplate_demo.yml`

```yaml
---
- name: Webhook -> Controller Job Template (AAP)
  hosts: all

  sources:
    - name: lab_webhook
      ansible.eda.pg_listener:
        channels:
          - lab_webhook

  rules:
    - name: Dispara Job Template quando severity=high
      condition: event.payload.payload.severity == "high"
      actions:
        - print_event:
            pretty: true
        - run_job_template:
            organization: Default
            name: Demo - Remediate Host
            job_args:
              extra_vars:
                host: "{{ event.payload.payload.host | default('unknown') }}"
                severity: "{{ event.payload.payload.severity | default('unknown') }}"
                message: "{{ event.payload.payload.message | default('') }}"
                event_id: "{{ event.payload.id | default(event.meta.uuid | default('')) }}"
                sent_at: "{{ event.payload.sent_at | default('') }}"
```

### Playbook do Controller

Arquivo: `playbooks/remediate_host.yml`

```yaml
---
- name: Demo remediation (placeholder)
  hosts: all
  gather_facts: false
  tasks:
    - name: Show vars from EDA
      ansible.builtin.debug:
        msg:
          host: "{{ host | default('') }}"
          severity: "{{ severity | default('') }}"
          message: "{{ message | default('') }}"
          event_id: "{{ event_id | default('') }}"
          sent_at: "{{ sent_at | default('') }}"
```

## Passo a passo reprodutível

### 1. Criar o Job Template no Controller

```bash
export EDA_BASE_URL='https://sandbox-aap-danielamaral789-dev.apps.rm1.0a51.p1.openshiftapps.com'
python3 scripts/create_controller_job_template_demo.py --base-url "$EDA_BASE_URL"
```

Esse comando cria ou ajusta:

- `Project` no Controller;
- `Inventory`;
- host `localhost`;
- `Job Template` `Demo - Remediate Host`.

### 2. Criar a credencial AAP no EDA

```bash
export EDA_BASE_URL='https://sandbox-aap-danielamaral789-dev.apps.rm1.0a51.p1.openshiftapps.com'
python3 scripts/create_eda_aap_controller_credential.py \
  --base-url "$EDA_BASE_URL" \
  --name aap-local-controller \
  --host "$EDA_BASE_URL/api/controller/"
```

Sem essa credencial, a `Activation` não consegue executar `run_job_template`.

### 3. Criar Project + Activation no EDA

```bash
export EDA_BASE_URL='https://sandbox-aap-danielamaral789-dev.apps.rm1.0a51.p1.openshiftapps.com'
python3 scripts/create_eda_hello_webhook_stack.py \
  --base-url "$EDA_BASE_URL" \
  --project-name 'testes-eda-project' \
  --project-url 'https://github.com/danielamaral789/testes_eda.git' \
  --project-branch 'main' \
  --rulebook 'jobtemplate_demo.yml' \
  --activation 'activation-jobtemplate-demo' \
  --decision-environment 'de-hello-webhook' \
  --de-image 'quay.io/ansible/ansible-rulebook:v1.2.1' \
  --event-stream 'lab-webhook' \
  --eda-credential-id <EDA_CRED_ID>
```

Substitua `<EDA_CRED_ID>` pelo id retornado na criação da credencial `aap-local-controller`.

### 4. Enviar um evento de teste

Payload de exemplo:

```json
{
  "id": "manual-test",
  "sent_at": "2026-03-08T00:00:00Z",
  "sequence": 1,
  "source": "testes_eda",
  "type": "synthetic",
  "payload": {
    "severity": "high",
    "host": "localhost",
    "message": "EDA -> Controller job template demo"
  }
}
```

Envio do evento:

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

Verifique os logs da `Activation`:

- UI: `Rulebook Activations` → `activation-jobtemplate-demo` → `History` → instância → `Logs`
- esperado:
  - recebimento do evento;
  - execução da action `run_job_template`;
  - tentativa de conexão com o Controller.

### Validação no Controller

Use:

```bash
export EDA_BASE_URL='https://sandbox-aap-danielamaral789-dev.apps.rm1.0a51.p1.openshiftapps.com'
python3 scripts/check_controller_latest_job.py --base-url "$EDA_BASE_URL" --job-template-id 9
```

Esperado:

- o último job aparece associado ao `Job Template`;
- o status final é `successful`.

## Problemas mais comuns

### Activation 400 ao criar ou atualizar

Erro típico:

```text
The rulebook requires a RH AAP credential.
```

Causa:

- a `Activation` foi criada sem a credencial AAP necessária para `run_job_template`.

Solução:

- criar a credencial com `scripts/create_eda_aap_controller_credential.py`;
- anexar o id da credencial usando `--eda-credential-id` no provisionamento da `Activation`.

### Condição do rulebook quebrando no motor de regras

Problema observado:

- certas expressões mais complexas podem causar falhas no motor Drools.

Solução adotada no lab:

- usar uma condição simples e direta:

```yaml
event.payload.payload.severity == "high"
```

### Job não recebe variáveis

Causa comum:

- o `Job Template` do Controller não aceita variáveis no lançamento.

Solução:

- garantir `ask_variables_on_launch=true` no template.

## Resultado esperado do cenário

Ao final do fluxo:

- o webhook recebe o evento com sucesso;
- o EDA processa o payload;
- o rulebook identifica `severity == "high"`;
- o Controller recebe o disparo do `Job Template`;
- o playbook executa usando os dados do evento.

## Relação com o restante do projeto

Este documento complementa o `README.md`:

- o `README.md` explica o projeto como um todo;
- este documento detalha apenas o cenário específico `EDA -> Controller`.
