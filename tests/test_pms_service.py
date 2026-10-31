"""
tests/test_pms_service.py — Suite de testes da Fase 1: Módulo PMS
Rececionista Virtual — Paraíso Villas & Spa

ATENÇÃO: Estes testes requerem:
  1. Newhotel Cloud acessível (https://www.newhotelcloud.com)
  2. Sessão autenticada no perfil Chrome configurado (ou login manual ao iniciar)
  3. Playwright instalado: playwright install chromium

Executar:
    python tests/test_pms_service.py

Os testes abrem o Chrome em modo visível (headless=False) para permitir
intervenção manual no login, se necessário.
"""

import sys
import json
import time
from pathlib import Path
from datetime import date, timedelta

# Adicionar a raiz do projeto ao path para imports relativos
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from paraiso_bot.services.pms_service import PMSService
from paraiso_bot.database.models import PMSQueryResult, TipologiaResult, NightlyRate

try:
    from paraiso_bot import config as cfg
    CHROME_DIR = str(cfg.CHROME_PMS_PROFILE_DIR)
    SCREENSHOTS_DIR = str(cfg.AUDIT_SCREENSHOTS_DIR)
except ImportError:
    CHROME_DIR = str(Path(__file__).parent.parent / "data" / "chrome_profiles" / "newhotel")
    SCREENSHOTS_DIR = str(Path(__file__).parent.parent / "data" / "audit_screenshots")

# ---------------------------------------------------------------------------
# Helpers de teste
# ---------------------------------------------------------------------------

_passed = 0
_failed = 0


def _ok(msg: str) -> None:
    global _passed
    _passed += 1
    print(f"  ✓ {msg}")


def _fail(msg: str) -> None:
    global _failed
    _failed += 1
    print(f"  ✗ FALHOU: {msg}")


def _assert(condition: bool, msg: str) -> None:
    if condition:
        _ok(msg)
    else:
        _fail(msg)


def _create_service() -> PMSService:
    return PMSService(
        chrome_user_data_dir=CHROME_DIR,
        audit_screenshots_dir=SCREENSHOTS_DIR,
    )


# ---------------------------------------------------------------------------
# Teste 1: Pesquisa básica — disponibilidade + preços
# ---------------------------------------------------------------------------

def test_basic_search() -> None:
    """
    Pesquisa com datas futuras (7 dias a partir de hoje), 2 pax, regime BB.
    Valida: sucesso, tipologias retornadas, estrutura de dados.
    """
    print("\n=== TESTE 1: Pesquisa básica (checkin +7 dias, 2 noites, 2 pax, BB) ===")

    svc = _create_service()
    svc.start()

    try:
        checkin = date.today() + timedelta(days=7)
        result = svc.search_availability(checkin=checkin, nights=2, pax=2, pension="BB")

        print(f"  Sucesso: {result.success}")
        print(f"  Erro: {result.error_message}")
        print(f"  Tipologias encontradas: {[t.code for t in result.tipologias]}")

        for t in result.tipologias:
            print(f"  {t.code}: disponível={t.is_available}, total={t.total_price:.2f}€")
            for nr in t.nightly_rates:
                status = "✓" if nr.is_available else "✗ INDISPONÍVEL"
                print(f"    {nr.date}: {nr.price:.2f}€ — {nr.available} unid. {status}")

        # Validações
        _assert(result.success, "Pesquisa concluída com sucesso")
        _assert(len(result.tipologias) > 0, "Pelo menos uma tipologia retornada")
        _assert(result.nights == 2, "Número de noites correto (2)")
        _assert(result.pax == 2, "Número de pax correto (2)")
        _assert(result.pension == "BB", "Regime correto (BB)")
        _assert(result.checkin == checkin, "Data de checkin correta")
        _assert(result.checkout == checkin + timedelta(days=2), "Data de checkout correta")

        for t in result.tipologias:
            _assert(t.code in ["T0", "T1", "T2"], f"Código tipologia válido: {t.code}")
            _assert(isinstance(t.total_price, float), f"{t.code}: total_price é float")
            _assert(isinstance(t.is_available, bool), f"{t.code}: is_available é bool")
            _assert(len(t.nightly_rates) == 2, f"{t.code}: 2 entradas de noites")
            for nr in t.nightly_rates:
                _assert(isinstance(nr.price, float), f"{t.code}/{nr.date}: price é float")
                _assert(isinstance(nr.available, int), f"{t.code}/{nr.date}: available é int")
                _assert(nr.price >= 0, f"{t.code}/{nr.date}: price >= 0")

        # Verificar screenshot de auditoria
        if result.screenshot_path:
            _assert(Path(result.screenshot_path).exists(), "Screenshot de auditoria guardado em disco")
        else:
            print("  ⚠ Screenshot não guardado (verificar permissões da pasta)")

    finally:
        svc.stop()


# ---------------------------------------------------------------------------
# Teste 2: Lógica de alternativas
# ---------------------------------------------------------------------------

def test_alternatives() -> None:
    """
    Testa a lógica de pesquisas alternativas.
    Usa datas próximas (amanhã) que podem estar esgotadas.
    Valida: máx. 3 pesquisas, datas diferentes, estrutura correta.
    """
    print("\n=== TESTE 2: Alternativas (checkin amanhã, 2 noites, 2 pax) ===")

    svc = _create_service()
    svc.start()

    try:
        checkin = date.today() + timedelta(days=1)
        results = svc.find_availability_with_alternatives(
            checkin=checkin, nights=2, pax=2
        )

        print(f"  Número de pesquisas realizadas: {len(results)}")
        for i, r in enumerate(results):
            disponivel = [t.code for t in r.get_available_tipologias()]
            print(f"  Pesquisa {i+1}: checkin={r.checkin}, sucesso={r.success}, "
                  f"disponíveis={disponivel or 'nenhuma'}")

        # Validações
        _assert(len(results) >= 1, "Pelo menos 1 pesquisa realizada")
        _assert(len(results) <= 3, "Máximo 3 pesquisas realizadas")

        # As datas das pesquisas alternativas devem ser diferentes da original
        if len(results) >= 2:
            _assert(
                results[1].checkin != results[0].checkin,
                "Alternativa 1 tem data diferente da original"
            )
        if len(results) >= 3:
            _assert(
                results[2].checkin != results[1].checkin,
                "Alternativa 2 tem data diferente da alternativa 1"
            )

        # Verificar que todas as pesquisas têm a estrutura correta
        for i, r in enumerate(results):
            _assert(
                isinstance(r, PMSQueryResult),
                f"Pesquisa {i+1} é instância de PMSQueryResult"
            )
            _assert(r.nights == 2, f"Pesquisa {i+1}: noites=2")

    finally:
        svc.stop()


# ---------------------------------------------------------------------------
# Teste 3: Estrutura de dados JSON
# ---------------------------------------------------------------------------

def test_json_serialization() -> None:
    """
    Valida a serialização/deserialização JSON dos resultados.
    Fundamental para guardar na base de dados SQLite.
    """
    print("\n=== TESTE 3: Serialização JSON (checkin +14 dias, 3 noites) ===")

    svc = _create_service()
    svc.start()

    try:
        checkin = date.today() + timedelta(days=14)
        result = svc.search_availability(checkin=checkin, nights=3, pax=2, pension="BB")

        if result.success and result.tipologias:
            # Testar serialização para JSON (para guardar na BD)
            json_str = result.to_json()
            _assert(len(json_str) > 0, "JSON serializado não está vazio")

            # Testar deserialização (para ler da BD)
            restored = PMSQueryResult.from_json(json_str)
            _assert(restored.checkin == result.checkin, "checkin restaurado corretamente")
            _assert(restored.nights == result.nights, "nights restaurado corretamente")
            _assert(restored.pax == result.pax, "pax restaurado corretamente")
            _assert(len(restored.tipologias) == len(result.tipologias), "Tipologias restauradas")

            # Testar estrutura de TipologiaResult
            for t in result.tipologias:
                _assert(t.code in ["T0", "T1", "T2"], f"Código válido: {t.code}")
                _assert(t.total_price >= 0.0, f"{t.code}: preço total positivo")
                _assert(
                    len(t.nightly_rates) == 3,
                    f"{t.code}: exatamente 3 entradas de noites"
                )

            print(f"  Estrutura JSON válida para {len(result.tipologias)} tipologia(s)")
            print(f"  Tamanho do JSON: {len(json_str)} caracteres")

        else:
            print(f"  Pesquisa sem resultados (normal fora de época): {result.error_message}")
            print("  A testar serialização de resultado de erro...")
            json_str = result.to_json()
            restored = PMSQueryResult.from_json(json_str)
            _assert(restored.success == result.success, "success restaurado corretamente")
            _assert(restored.error_message == result.error_message, "error_message restaurado")
            _ok("Serialização de resultado de erro funciona corretamente")

    finally:
        svc.stop()


# ---------------------------------------------------------------------------
# Teste 4: Filtro por tipologia específica
# ---------------------------------------------------------------------------

def test_tipologia_filter() -> None:
    """
    Testa a pesquisa filtrada por tipologia específica (T1).
    Valida que apenas a tipologia pedida é retornada.
    """
    print("\n=== TESTE 4: Filtro por tipologia T1 (checkin +10 dias, 2 noites, 2 pax) ===")

    svc = _create_service()
    svc.start()

    try:
        checkin = date.today() + timedelta(days=10)
        result = svc.search_availability(
            checkin=checkin, nights=2, pax=2, pension="BB", tipologia="T1"
        )

        print(f"  Sucesso: {result.success}")
        print(f"  Tipologias retornadas: {[t.code for t in result.tipologias]}")

        if result.success and result.tipologias:
            # Quando filtramos por T1, apenas T1 deve aparecer
            codes = [t.code for t in result.tipologias]
            _assert("T1" in codes or len(codes) == 0, "Apenas T1 ou lista vazia retornada")
            _assert(all(c == "T1" for c in codes), "Todos os resultados são T1")
        else:
            print(f"  Sem resultados para T1: {result.error_message}")
            _ok("Filtro por tipologia não causou crash")

    finally:
        svc.stop()


# ---------------------------------------------------------------------------
# Teste 5: Tratamento de erros — modelo de dados de erro
# ---------------------------------------------------------------------------

def test_error_result_model() -> None:
    """
    Testa a criação de PMSQueryResult de erro sem necessidade de browser.
    Valida o método factory error_result().
    """
    print("\n=== TESTE 5: Modelo de resultado de erro (sem browser) ===")

    checkin = date(2026, 12, 1)
    error_result = PMSQueryResult.error_result(
        checkin=checkin,
        nights=2,
        pax=2,
        pension="BB",
        error_message="timeout",
    )

    _assert(error_result.success is False, "success=False")
    _assert(error_result.error_message == "timeout", "error_message='timeout'")
    _assert(error_result.tipologias == [], "tipologias=[]")
    _assert(error_result.checkin == checkin, "checkin correto")
    _assert(error_result.nights == 2, "nights=2")
    _assert(len(error_result.to_json()) > 0, "Serialização JSON funciona mesmo com erro")
    _assert(not error_result.has_any_availability(), "has_any_availability()=False")
    _assert(error_result.get_available_tipologias() == [], "get_available_tipologias()=[]")
    _assert(error_result.get_tipologia("T0") is None, "get_tipologia('T0')=None")

    print("  (Este teste não abre o browser)")


# ---------------------------------------------------------------------------
# Teste 6: Lógica de datas alternativas (sem browser)
# ---------------------------------------------------------------------------

def test_alternative_date_logic() -> None:
    """
    Testa os métodos de cálculo de datas alternativas sem precisar do browser.
    Valida: _is_weekend_period, _next_weekend, _next_available_weekday.
    """
    print("\n=== TESTE 6: Lógica de datas alternativas (sem browser) ===")

    # Criar serviço apenas para aceder aos métodos (sem start())
    svc = PMSService(
        chrome_user_data_dir=CHROME_DIR,
        audit_screenshots_dir=SCREENSHOTS_DIR,
    )

    # Sábado (2026-11-07) + 2 noites = inclui fim de semana
    saturday = date(2026, 11, 7)
    _assert(svc._is_weekend_period(saturday, 2), "Sábado + 2 noites = inclui fim de semana")

    # Segunda-feira (2026-11-02) + 2 noites = apenas dias úteis
    monday = date(2026, 11, 2)
    _assert(not svc._is_weekend_period(monday, 2), "Segunda + 2 noites = só dias úteis")

    # Próximo sábado a partir de terça
    tuesday = date(2026, 11, 3)  # terça-feira
    next_sat = svc._next_weekend(tuesday)
    _assert(next_sat.weekday() == 5, f"_next_weekend({tuesday}) retorna sábado: {next_sat}")
    _assert(next_sat > tuesday, "Próximo sábado é no futuro")

    # Próximo dia útil a partir de domingo
    sunday = date(2026, 11, 8)  # domingo
    next_weekday = svc._next_available_weekday(sunday)
    _assert(next_weekday.weekday() < 5, f"_next_available_weekday({sunday}) retorna dia útil: {next_weekday}")
    _assert(next_weekday == date(2026, 11, 9), "Próximo dia útil após domingo é segunda")

    # Próximo dia útil a partir de sexta
    friday = date(2026, 11, 6)
    next_weekday_from_friday = svc._next_available_weekday(friday)
    _assert(next_weekday_from_friday.weekday() < 5, "Próximo dia útil após sexta é dia útil")

    print("  (Este teste não abre o browser)")


# ---------------------------------------------------------------------------
# Teste 7: Parsing de preços e disponibilidade (sem browser)
# ---------------------------------------------------------------------------

def test_price_parsing() -> None:
    """
    Testa os métodos estáticos de parsing de preços e disponibilidade.
    """
    print("\n=== TESTE 7: Parsing de preços e disponibilidade (sem browser) ===")

    # Preços em formato europeu
    _assert(PMSService._parse_price("234,56 €") == 234.56, "Formato europeu simples: 234,56 €")
    _assert(PMSService._parse_price("1.234,56 €") == 1234.56, "Formato europeu com milhar: 1.234,56 €")
    _assert(PMSService._parse_price("150.00") == 150.00, "Formato anglo-saxónico: 150.00")
    _assert(PMSService._parse_price("€ 99") == 99.0, "Símbolo antes do valor: € 99")
    _assert(PMSService._parse_price("") is None, "Texto vazio → None")
    _assert(PMSService._parse_price("N/A") is None, "Texto inválido → None")

    # Disponibilidade por texto
    _assert(PMSService._parse_availability("5") == 5, "Disponibilidade numérica: 5")
    _assert(PMSService._parse_availability("0") == 0, "Disponibilidade zero: 0")
    _assert(PMSService._parse_availability("") is None, "Texto vazio → None")

    # Disponibilidade por cor de fundo alaranjada (indisponível)
    orange_bg = "rgb(255, 200, 100)"  # alaranjado → indisponível
    _assert(
        PMSService._parse_availability("1", orange_bg) == 0,
        "Fundo alaranjado → indisponível (0) mesmo com texto '1'"
    )

    # Fundo neutro (disponível)
    neutral_bg = "rgb(255, 255, 255)"  # branco → não interfere
    _assert(
        PMSService._parse_availability("3", neutral_bg) == 3,
        "Fundo branco + texto '3' → disponibilidade 3"
    )

    print("  (Este teste não abre o browser)")


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("  FASE 1 — TESTES DO MÓDULO PMS SERVICE")
    print("  Paraíso Villas & Spa — Rececionista Virtual")
    print("=" * 60)

    # Testes sem browser (sempre correm)
    test_error_result_model()
    test_alternative_date_logic()
    test_price_parsing()

    # Testes com browser (requerem Newhotel acessível)
    run_browser_tests = input(
        "\nExecutar testes com browser (requer Newhotel autenticado)? [s/N] "
    ).strip().lower()

    if run_browser_tests in ("s", "sim", "y", "yes"):
        test_basic_search()
        test_alternatives()
        test_json_serialization()
        test_tipologia_filter()
    else:
        print("\n(Testes com browser ignorados)")

    # Resumo
    print("\n" + "=" * 60)
    total = _passed + _failed
    print(f"  RESULTADO: {_passed}/{total} testes passaram")
    if _failed > 0:
        print(f"  ✗ {_failed} teste(s) FALHARAM — verificar saída acima")
    else:
        print("  ✓ Todos os testes passaram!")
    print("=" * 60)

    if _failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
