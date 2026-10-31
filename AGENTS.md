# VpsOracleHunter

Automat Windows/PowerShell, który ponawia próby, aż Oracle Cloud zwolni pojemność, i wtedy tworzy jedną darmową (Always Free) instancję `VM.Standard.A1.Flex` (2 OCPU, 12 GB RAM, Ubuntu 24.04 ARM64). Szczegóły w `README.md`.

## Uruchomienie

```powershell
uv venv .venv
uv pip install --python .venv oci-cli
.\scripts\start.ps1      # w tle
.\scripts\status.ps1     # stan łapacza i instancji w OCI
.\scripts\stop.ps1       # zatrzymuje tylko lokalny proces
```

Testów w repo nie ma.

## Twarde reguły

- Konfiguracja jest walidowana na sztywno: dozwolone tylko `VM.Standard.A1.Flex`, 2 OCPU, 12 GB. Nie poluzowuj tego, bo błąd konfiguracji tworzy płatną instancję.
- Instancja zawsze on-demand: nie przekazuj `--preemptible-instance-config`. Nie dodawaj dysków blokowych, load balancerów ani zmiany rozmiaru boot volume.
- Łapacz kończy się po pierwszym sukcesie albo twardym błędzie (`LimitExceeded`, złe dane logowania, zły parametr). Nie automatyzuj jego restartu po restarcie systemu: README nazywa to celowym.
- Log zawiera tylko znaczniki czasu, numery rund, AD, wyniki i kody błędów. Nigdy kluczy ani tokenów.

## Mapa

- `scripts/catch-a1.ps1`: pętla łapacza (preflight, rundy po AD, backoff, sukces).
- `scripts/start.ps1`, `status.ps1`, `stop.ps1`: uruchomienie w tle, stan, zatrzymanie.
- `config/catcher.psd1`: parametry niesekretne (region, nazwa VM, interwał ponowień).

## Dane lokalne

- `.secrets/` (klucze OCI i SSH), `.venv/` i `var/` (logi, `var/runtime/resolved.json`, `var/runtime/success.json`) są ignorowane przez git. Nie commituj ich zawartości.

## Git

- Commit: nagłówek Conventional Commits po angielsku, `type(scope): opis`, ze scope'em z listy w `.pre-commit-config.yaml`; po pustej linii streszczenie „co i dlaczego”; potem sekcja `Validation:` z kontrolami uruchomionymi na finalnym indeksie. Format: https://github.com/MattyMroz/agents/blob/main/presets/hooks/references/commit-message.md
- Kontrole uruchamiaj przez `mm-validate run "<kontrola>" -- <komenda>`, blok `Validation:` bierz z `mm-validate list`. Hooki `conventional-pre-commit` i `commit-validation` odrzucą inny commit.
- Pliki dodawaj po nazwie, nigdy `git add .`; bez `--no-verify`. Worktree'y tylko w `.worktrees/<nazwa>`.
