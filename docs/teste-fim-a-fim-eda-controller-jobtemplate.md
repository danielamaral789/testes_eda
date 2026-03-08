# Teste fim-a-fim: Webhook (Event Stream) → EDA Rulebook → Job Template (Controller)

Data do teste: **2026-03-08**

Este documento descreve o teste completo que fizemos no lab para validar:

1) recebimento de eventos via **Event Stream** (webhook) no EDA  
2) execução de um **rulebook** no EDA (Activation)  
3) disparo de um **Job Template** no **Automation Controller** (AAP) *a partir do EDA* (action `run_job_template`)

## Visão geral do fluxo

1. Um evento JSON é enviado para a URL do webhook do Event Stream `lab-webhook`.
2. O EDA entrega esse evento para o rulebook (`jobtemplate_demo.yml`) via `ansible.eda.pg_listener` (source `lab_webhook`).
3. Se `severity == "high"`, o rulebook chama o Job Template `Demo - Remediate Host` no Controller.
4. O Controller executa o playbook `playbooks/remediate_host.yml` (neste repo) e o job finaliza com `successful`.

## Componentes criados/usados

### EDA
- **Event Stream**: `lab-webhook` (webhook + token)
- **EDA Credential (AAP)**: `aap-local-controller` (Credential Type: `Red Hat Ansible Automation Platform`)
- **Project (EDA)**: `testes-eda-project` (Git URL do repo `testes_eda`)
- **Rulebook**: `jobtemplate_demo.yml`
- **Activation**: `activation-jobtemplate-demo` (mapeando Event Stream `lab-webhook` → source `lab_webhook`)

### Controller (AAP)
- **Project**: `controller-testes-eda-project` (Git URL do repo `testes_eda`)
- **Inventory**: `eda-demo-inventory` com host `localhost` e variável `ansible_connection: local`
- **Job Template**: `Demo - Remediate Host` (playbook `playbooks/remediate_host.yml`, `ask_variables_on_launch=true`)

## Trechos de código (os que fazem o teste acontecer)

### Rulebook (EDA): `jobtemplate_demo.yml`

> O EDA carrega este arquivo pelo Project (Git) e cria o source `lab_webhook`, que é mapeado ao Event Stream `lab-webhook`.

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

### Playbook (Controller): `playbooks/remediate_host.yml`

> O Job Template executa este playbook; aqui ele só imprime as variáveis vindas do evento (demo).

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

## Passo a passo (reprodutível)

### 1) Criar o Job Template no Controller

Cria/ajusta Project + Inventory(localhost) + Job Template no Controller:

```bash
export EDA_BASE_URL='https://sandbox-aap-danielamaral789-dev.apps.rm1.0a51.p1.openshiftapps.com'
python3 scripts/create_controller_job_template_demo.py --base-url "$EDA_BASE_URL"
```

### 2) Criar a credencial AAP no EDA (requisito do `run_job_template`)

Sem isso, a Activation falha com `The rulebook requires a RH AAP credential.`.

```bash
export EDA_BASE_URL='https://sandbox-aap-danielamaral789-dev.apps.rm1.0a51.p1.openshiftapps.com'
python3 scripts/create_eda_aap_controller_credential.py \
  --base-url "$EDA_BASE_URL" \
  --name aap-local-controller \
  --host "$EDA_BASE_URL/api/controller/"
```

### 3) Criar Project + Activation no EDA (usando este repo)

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

> Observação: substitua `<EDA_CRED_ID>` pelo id retornado na criação da credencial `aap-local-controller`.

### 4) Enviar evento “high” (dispara o Job Template)

Exemplo de payload:

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

Envio via script (com token do Event Stream no header `Authorization`):

```bash
export EDA_WEBHOOK_URL='https://<...>/eda-event-streams/api/eda/v1/external_event_stream/<uuid>/post/'
TOKEN="$(cat local/lab-webhook.token)"

python3 scripts/send_webhook_events.py \
  --url "$EDA_WEBHOOK_URL" \
  --header "Authorization: $TOKEN" \
  --count 1 \
  --data '<JSON_ACIMA>'
```

## Como validar que funcionou

### Validação no EDA
- UI: **Rulebook Activations** → `activation-jobtemplate-demo` → **History** → instância → **Logs**
- Esperado: logs com `Attempting to connect to Controller ...` e, após evento, execução da action `run_job_template`.

### Validação no Controller
Checar o último job do Job Template:

```bash
export EDA_BASE_URL='https://sandbox-aap-danielamaral789-dev.apps.rm1.0a51.p1.openshiftapps.com'
python3 scripts/check_controller_latest_job.py --base-url "$EDA_BASE_URL" --job-template-id 9
```

Esperado: status `successful`.

## Troubleshooting (os problemas reais que apareceram no lab)

- **Activation 400**: `"The rulebook requires a RH AAP credential."`  
  Solução: anexar a credencial AAP no campo `eda_credentials` da Activation (API/UI).

- **Crash no Drools (NullPointerException)** ao usar `is mapping` em condição  
  Solução: usar uma condição simples e direta (no nosso caso, `event.payload.payload.severity == "high"`).
