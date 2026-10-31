"""
services/pms_service.py — Módulo de consulta ao PMS Newhotel Cloud
Automação via Playwright (Chrome com perfil persistente, headless=False).

Regras absolutas deste módulo:
  - APENAS LEITURA do Newhotel — nunca alterar dados
  - Nunca inventar preços ou disponibilidade
  - Em caso de falha, retornar PMSQueryResult(success=False) — nunca lançar exceção não tratada
"""

import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

# Importação dos módulos do projeto
from paraiso_bot.database.models import NightlyRate, PMSQueryResult, TipologiaResult
from paraiso_bot.core.logger import ActivityLogger


# ---------------------------------------------------------------------------
# Constantes internas do módulo
# ---------------------------------------------------------------------------
# Seletor CSS da zona de filtros (área de pesquisa no topo da página)
_SEL_FILTER_ZONE = ".rates-lookup, form, .search-filters, [class*='filter'], [class*='lookup']"

# Timeout padrão para esperar elementos (ms)
_DEFAULT_TIMEOUT_MS = 15_000

# Máximo de noites para usar botões +/- em vez de fill direto
_MAX_NIGHTS_BUTTON_CLICK = 30


class PMSService:
    """
    Serviço de consulta ao Newhotel Cloud via automação de browser.

    Uso típico:
        svc = PMSService(chrome_user_data_dir="...", audit_screenshots_dir="...")
        svc.start()
        result = svc.search_availability(checkin=date(2026, 12, 1), nights=2, pax=2)
        svc.stop()
    """

    def __init__(self, chrome_user_data_dir: str, audit_screenshots_dir: str):
        """
        Parâmetros:
            chrome_user_data_dir   : Pasta do perfil Chrome persistente (sessão mantida).
            audit_screenshots_dir  : Pasta onde guardar screenshots de auditoria.
        """
        self._chrome_user_data_dir = str(chrome_user_data_dir)
        self._audit_dir = Path(audit_screenshots_dir)
        self._audit_dir.mkdir(parents=True, exist_ok=True)

        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

        self._log = ActivityLogger("pms_service")

        # Limpar screenshots com mais de 7 dias na inicialização
        self._cleanup_old_screenshots()

    # -----------------------------------------------------------------------
    # Ciclo de vida do browser
    # -----------------------------------------------------------------------

    def start(self) -> None:
        """
        Inicia o Playwright e abre o Chrome com perfil persistente.
        Navega para a página do Newhotel e trata do login se necessário.
        """
        self._log.info("A iniciar o browser Chrome para o Newhotel PMS...")

        try:
            from paraiso_bot import config as cfg
            slow_mo = cfg.BROWSER_SLOW_MO
            viewport_w = cfg.BROWSER_VIEWPORT_WIDTH
            viewport_h = cfg.BROWSER_VIEWPORT_HEIGHT
            newhotel_url = cfg.NEWHOTEL_URL
        except ImportError:
            slow_mo = 200
            viewport_w = 1280
            viewport_h = 900
            newhotel_url = "https://www.newhotelcloud.com/#/clubeparaiso/pms/(rateslookup)"

        self._playwright = sync_playwright().start()

        # Lançar Chrome com perfil persistente (mantém sessão entre reinícios)
        # headless=False: necessário para poder observar e para evitar deteção de bot
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=self._chrome_user_data_dir,
            headless=False,
            slow_mo=slow_mo,
            viewport={"width": viewport_w, "height": viewport_h},
            args=[
                "--disable-blink-features=AutomationControlled",  # Evitar deteção de automação
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        # Usar a primeira página existente ou abrir nova
        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = self._context.new_page()

        self._log.info(f"Browser iniciado. A navegar para: {newhotel_url}")
        self._page.goto(newhotel_url, wait_until="domcontentloaded", timeout=30_000)

        # Verificar se é necessário fazer login
        self._handle_login_if_needed()

        self._log.info("PMSService pronto para pesquisas.")

    def stop(self) -> None:
        """Fecha o browser de forma limpa."""
        self._log.info("A fechar o browser Chrome do Newhotel PMS...")
        try:
            if self._context:
                self._context.close()
            if self._playwright:
                self._playwright.stop()
        except Exception as exc:
            self._log.warning(f"Erro ao fechar browser: {exc}")
        finally:
            self._context = None
            self._page = None
            self._playwright = None
        self._log.info("Browser fechado.")

    # -----------------------------------------------------------------------
    # API pública — pesquisa de disponibilidade
    # -----------------------------------------------------------------------

    def search_availability(
        self,
        checkin: date,
        nights: int,
        pax: int,
        pension: str = "BB",
        tipologia: Optional[str] = None,
    ) -> PMSQueryResult:
        """
        Executa uma pesquisa de disponibilidade e preços no Newhotel Cloud.

        Parâmetros:
            checkin   : Data de chegada.
            nights    : Número de noites.
            pax       : Total de hóspedes (adultos + crianças, campo principal).
            pension   : "BB" (c/ pequeno-almoço) ou "RO" (só alojamento).
            tipologia : None = todas as tipologias; "T0"/"T1"/"T2" = filtrar.

        Retorna:
            PMSQueryResult com todos os dados extraídos, ou com success=False em caso de erro.
        """
        checkout = checkin + timedelta(days=nights)
        pension_upper = pension.upper()

        self._log.info(
            f"A pesquisar: checkin={checkin}, nights={nights}, pax={pax}, "
            f"pension={pension_upper}, tipologia={tipologia or 'todas'}"
        )

        try:
            # 1. Garantir que estamos na página correta
            self._ensure_on_rateslookup_page()

            # 2. Limpar filtros e preencher com os valores pedidos
            self._clear_and_fill_filters(
                checkin=checkin,
                nights=nights,
                pax=pax,
                pension=pension_upper,
                tipologia=tipologia,
            )

            # 3. Clicar em Procurar e aguardar resultados
            self._click_search()

            # 4. Extrair os dados da grelha de resultados
            tipologia_results = self._extract_results(nights=nights)

            # 5. Screenshot de auditoria (apenas uso interno — nunca enviado ao cliente)
            screenshot_path = self._take_audit_screenshot(
                prefix=f"checkin_{checkin.strftime('%Y%m%d')}_nights_{nights}"
            )

            # 6. Filtrar para tipologia específica se pedido
            if tipologia:
                tipologia_upper = tipologia.upper()
                tipologia_results = [t for t in tipologia_results if t.code == tipologia_upper]

            self._log.info(
                f"Pesquisa concluída: {len(tipologia_results)} tipologia(s) encontradas; "
                f"disponíveis: {sum(1 for t in tipologia_results if t.is_available)}"
            )

            return PMSQueryResult(
                checkin=checkin,
                checkout=checkout,
                nights=nights,
                pax=pax,
                pension=pension_upper,
                tipologias=tipologia_results,
                screenshot_path=screenshot_path,
                query_timestamp=datetime.now().isoformat(),
                success=True,
            )

        except PlaywrightTimeoutError as exc:
            msg = f"Timeout na pesquisa Newhotel: {exc}"
            self._log.error(msg)
            return PMSQueryResult.error_result(checkin, nights, pax, pension_upper, "timeout")

        except _LoginRequiredError:
            msg = "Sessão Newhotel expirada — necessário novo login manual."
            self._log.error(msg)
            return PMSQueryResult.error_result(checkin, nights, pax, pension_upper, "login_required")

        except _NetworkError as exc:
            msg = f"Erro de rede ao aceder ao Newhotel: {exc}"
            self._log.error(msg)
            return PMSQueryResult.error_result(checkin, nights, pax, pension_upper, "network_error")

        except _NoResultsError:
            self._log.warning("Pesquisa não retornou resultados (página vazia ou sem tipologias).")
            return PMSQueryResult.error_result(checkin, nights, pax, pension_upper, "no_results")

        except Exception as exc:
            msg = f"Erro inesperado na pesquisa PMS: {type(exc).__name__}: {exc}"
            self._log.error(msg, details=str(exc))
            return PMSQueryResult.error_result(checkin, nights, pax, pension_upper, f"unexpected: {exc}")

    def find_availability_with_alternatives(
        self,
        checkin: date,
        nights: int,
        pax: int,
        pension: str = "BB",
        tipologia: Optional[str] = None,
    ) -> list[PMSQueryResult]:
        """
        Pesquisa original + até 2 alternativas se não houver disponibilidade.

        Regras de alternativas (conforme especificação):
          - Se o período inclui fim de semana → propor próximo fim de semana
          - Se é durante a semana → propor próximo dia disponível (avançar 1 dia, máx. 7 tentativas)
          - Máximo 3 consultas no total (original + alt1 + alt2)

        Retorna lista com 1 a 3 PMSQueryResult.
        """
        results: list[PMSQueryResult] = []

        # --- Pesquisa original ---
        self._log.info(f"Pesquisa 1/3 (original): checkin={checkin}")
        r1 = self.search_availability(checkin, nights, pax, pension, tipologia)
        results.append(r1)

        if r1.success and r1.has_any_availability():
            self._log.info("Disponibilidade encontrada na pesquisa original. Sem necessidade de alternativas.")
            return results

        # --- Alternativa 1 ---
        alt1_checkin = self._compute_alternative_date(checkin, nights, attempt=1)
        if alt1_checkin is None:
            self._log.info("Sem datas alternativas calculáveis para alternativa 1.")
            return results

        self._log.info(f"Pesquisa 2/3 (alternativa 1): checkin={alt1_checkin}")
        r2 = self.search_availability(alt1_checkin, nights, pax, pension, tipologia)
        results.append(r2)

        if r2.success and r2.has_any_availability():
            self._log.info("Disponibilidade encontrada na alternativa 1.")
            return results

        # --- Alternativa 2 ---
        alt2_checkin = self._compute_alternative_date(alt1_checkin, nights, attempt=2)
        if alt2_checkin is None:
            self._log.info("Sem datas alternativas calculáveis para alternativa 2.")
            return results

        self._log.info(f"Pesquisa 3/3 (alternativa 2): checkin={alt2_checkin}")
        r3 = self.search_availability(alt2_checkin, nights, pax, pension, tipologia)
        results.append(r3)

        if r3.success and r3.has_any_availability():
            self._log.info("Disponibilidade encontrada na alternativa 2.")
        else:
            self._log.warning("Sem disponibilidade após 3 pesquisas. Informar o cliente.")

        return results

    # -----------------------------------------------------------------------
    # Métodos privados — Browser e página
    # -----------------------------------------------------------------------

    def _ensure_on_rateslookup_page(self) -> None:
        """
        Garante que o browser está na página de Procurar Tarifas.
        Navega para lá se necessário.
        """
        try:
            from paraiso_bot import config as cfg
            target_url = cfg.NEWHOTEL_URL
        except ImportError:
            target_url = "https://www.newhotelcloud.com/#/clubeparaiso/pms/(rateslookup)"

        current_url = self._page.url
        if "rateslookup" not in current_url:
            self._log.debug(f"URL atual ({current_url}) não é a página de tarifas. A navegar...")
            self._page.goto(target_url, wait_until="domcontentloaded", timeout=30_000)
            time.sleep(1)
            self._handle_login_if_needed()

    def _handle_login_if_needed(self) -> None:
        """
        Deteta se a página está a pedir login e aguarda intervenção manual
        durante até 60 segundos. Se o login não for concluído, levanta _LoginRequiredError.
        """
        try:
            from paraiso_bot import config as cfg
            wait_seconds = cfg.PMS_LOGIN_WAIT
        except ImportError:
            wait_seconds = 60

        # Verificar indicadores de página de login
        login_indicators = [
            "input[type='password']",
            "[class*='login']",
            "[class*='signin']",
            "text=Login",
            "text=Entrar",
        ]

        is_login_page = False
        for sel in login_indicators:
            try:
                if self._page.locator(sel).count() > 0:
                    is_login_page = True
                    break
            except Exception:
                pass

        if not is_login_page:
            return  # Já estamos autenticados, nada a fazer

        self._log.warning(
            f"Página de login detetada. Aguardando intervenção manual até {wait_seconds}s..."
        )

        # Aguardar que o utilizador faça login (a página deve mudar de URL ou conteúdo)
        try:
            self._page.wait_for_url(
                lambda url: "login" not in url.lower() and "signin" not in url.lower(),
                timeout=wait_seconds * 1000,
            )
            self._log.info("Login concluído com sucesso (detetado por mudança de URL).")
        except PlaywrightTimeoutError:
            self._log.error("Timeout a aguardar login. Não foi possível autenticar.")
            raise _LoginRequiredError("Login não concluído dentro do tempo limite.")

    def _clear_and_fill_filters(
        self,
        checkin: date,
        nights: int,
        pax: int,
        pension: str,
        tipologia: Optional[str] = None,
    ) -> None:
        """
        Limpa os filtros existentes e preenche com os novos valores.
        Estratégia de seletores: aria-label > placeholder > texto do label > posição relativa.
        """
        self._log.debug(
            f"A preencher filtros: checkin={checkin}, nights={nights}, pax={pax}, "
            f"pension={pension}, tipologia={tipologia}"
        )

        checkout = checkin + timedelta(days=nights)
        checkin_str = checkin.strftime("%d/%m/%Y")
        checkout_str = checkout.strftime("%d/%m/%Y")

        # ---- Passo 1: Clicar em "Limpar Filtros" (se existir) ----
        self._try_click_clear_filters()

        # ---- Passo 2: Preencher data de chegada ----
        self._fill_date_field("checkin", checkin_str)
        time.sleep(0.5)

        # ---- Passo 3: Preencher data de saída ----
        # (pode já ter sido calculada automaticamente pelo Newhotel; verificar primeiro)
        self._fill_date_field("checkout", checkout_str)
        time.sleep(0.5)

        # ---- Passo 4: Preencher número de noites ----
        self._fill_nights_field(nights)
        time.sleep(0.3)

        # ---- Passo 5: Selecionar regime de pensão ----
        self._select_pension(pension)
        time.sleep(0.3)

        # ---- Passo 6: Preencher número de pax (campo principal — adultos) ----
        self._fill_pax_field(pax)
        time.sleep(0.3)

        # ---- Passo 7: Selecionar tipologia (se especificada) ----
        if tipologia:
            self._select_tipologia(tipologia)
            time.sleep(0.3)

        self._log.debug("Filtros preenchidos com sucesso.")

    def _try_click_clear_filters(self) -> None:
        """Tenta clicar no botão 'Limpar Filtros'. Não falha se o botão não existir."""
        clear_selectors = [
            "button:has-text('Limpar Filtros')",
            "button:has-text('Limpar')",
            "button:has-text('Clear')",
            "[class*='clear']:has-text('Filtros')",
            "[class*='reset']",
        ]
        for sel in clear_selectors:
            try:
                btn = self._page.locator(sel).first
                if btn.count() > 0:
                    btn.click(timeout=3_000)
                    self._log.debug(f"Filtros limpos via seletor: {sel}")
                    time.sleep(0.5)
                    return
            except Exception:
                continue
        self._log.debug("Botão 'Limpar Filtros' não encontrado — a continuar sem limpar.")

    def _fill_date_field(self, field_type: str, date_str: str) -> None:
        """
        Preenche um campo de data (chegada ou saída).
        field_type: 'checkin' ou 'checkout'
        date_str  : Formato DD/MM/YYYY
        """
        # Seletores possíveis para os campos de data, por ordem de preferência
        if field_type == "checkin":
            selectors = [
                "input[aria-label*='Chegada']",
                "input[aria-label*='Check-in']",
                "input[placeholder*='Chegada']",
                "input[placeholder*='Check-in']",
                "input[name*='checkin']",
                "input[name*='arrival']",
                # Posição relativa: primeiro input de data
                "input[type='text']:nth-of-type(1)",
            ]
        else:  # checkout
            selectors = [
                "input[aria-label*='Saída']",
                "input[aria-label*='Check-out']",
                "input[placeholder*='Saída']",
                "input[placeholder*='Check-out']",
                "input[name*='checkout']",
                "input[name*='departure']",
                # Posição relativa: segundo input de data
                "input[type='text']:nth-of-type(2)",
            ]

        for sel in selectors:
            try:
                el = self._page.locator(sel).first
                if el.count() == 0:
                    continue
                # Triplo clique para selecionar todo o texto antes de substituir
                el.triple_click(timeout=5_000)
                el.fill(date_str, timeout=5_000)
                # Pressionar Tab para confirmar e mover para o próximo campo
                el.press("Tab")
                self._log.debug(f"Campo {field_type} preenchido com '{date_str}' via '{sel}'")
                return
            except Exception:
                continue

        # Fallback: tentar por texto adjacente ao label
        self._log.warning(
            f"Não foi possível preencher o campo {field_type}. "
            "Verificar seletores — o DOM do Newhotel pode ter mudado."
        )

    def _fill_nights_field(self, nights: int) -> None:
        """
        Preenche o campo de número de noites.
        Tenta fill direto; se falhar, usa botões +/-.
        """
        nights_selectors = [
            "input[aria-label*='Noites']",
            "input[aria-label*='Nights']",
            "input[placeholder*='Noite']",
            "input[name*='nights']",
            "input[name*='duration']",
        ]

        for sel in nights_selectors:
            try:
                el = self._page.locator(sel).first
                if el.count() == 0:
                    continue
                el.triple_click(timeout=5_000)
                el.fill(str(nights), timeout=5_000)
                el.press("Tab")
                self._log.debug(f"Campo noites preenchido com {nights} via '{sel}'")
                return
            except Exception:
                continue

        # Fallback: usar botões + e - do spinner
        self._fill_spinner_field("nights", nights)

    def _fill_pax_field(self, pax: int) -> None:
        """
        Preenche o campo principal de número de pessoas (adultos/total).
        O segundo campo (crianças, ícone de bolo) é ignorado — mantém 0.
        """
        pax_selectors = [
            "input[aria-label*='Pax']",
            "input[aria-label*='Adultos']",
            "input[aria-label*='Hóspedes']",
            "input[placeholder*='Pax']",
            "input[placeholder*='Adultos']",
            "input[name*='pax']",
            "input[name*='adults']",
            "input[name*='guests']",
        ]

        for sel in pax_selectors:
            try:
                # Usar .first para apanhar apenas o primeiro campo Pax (adultos)
                el = self._page.locator(sel).first
                if el.count() == 0:
                    continue
                el.triple_click(timeout=5_000)
                el.fill(str(pax), timeout=5_000)
                el.press("Tab")
                self._log.debug(f"Campo pax preenchido com {pax} via '{sel}'")
                return
            except Exception:
                continue

        # Fallback: spinner do primeiro campo pax
        self._fill_spinner_field("pax", pax)

    def _fill_spinner_field(self, field_name: str, target_value: int) -> None:
        """
        Usa botões +/- de um spinner para atingir o valor desejado.
        Usado como fallback quando fill direto não funciona.
        """
        self._log.debug(f"A usar botões +/- para o campo '{field_name}' → {target_value}")

        # Mapeamento de possíveis seletores para botões + e - por campo
        btn_plus_selectors = [
            f"button[aria-label*='+'][aria-label*='{field_name}']",
            f"[class*='{field_name}'] button[class*='plus']",
            f"[class*='{field_name}'] button[class*='increment']",
        ]
        btn_minus_selectors = [
            f"button[aria-label*='-'][aria-label*='{field_name}']",
            f"[class*='{field_name}'] button[class*='minus']",
            f"[class*='{field_name}'] button[class*='decrement']",
        ]

        # Primeiro, obter o valor atual
        try:
            current_val = 1  # Assumir 1 como valor mínimo por defeito
            # Tentar clicar + ou - conforme necessário
            diff = target_value - current_val
            if diff == 0:
                return

            btn_sel = btn_plus_selectors[0] if diff > 0 else btn_minus_selectors[0]
            for _ in range(abs(diff)):
                try:
                    self._page.locator(btn_sel).first.click(timeout=2_000)
                    time.sleep(0.1)
                except Exception:
                    break
        except Exception as exc:
            self._log.warning(f"Não foi possível usar spinner para '{field_name}': {exc}")

    def _select_pension(self, pension: str) -> None:
        """
        Seleciona o regime de pensão no dropdown.
        pension: "BB" (com Pequeno-Almoço) ou "RO" (Alojamento apenas)
        """
        try:
            from paraiso_bot import config as cfg
            pension_label = cfg.PENSION_LABELS.get(pension, "Alojamento + Pequeno-Almoço")
        except ImportError:
            pension_label = "Alojamento + Pequeno-Almoço" if pension == "BB" else "Alojamento"

        self._log.debug(f"A selecionar pensão: {pension} → '{pension_label}'")

        # Tentar como <select> nativo primeiro (mais simples e estável)
        select_selectors = [
            "select[name*='pension']",
            "select[name*='meal']",
            "select[name*='regime']",
            "select[aria-label*='Pensão']",
            "select[aria-label*='Regime']",
        ]

        for sel in select_selectors:
            try:
                el = self._page.locator(sel).first
                if el.count() == 0:
                    continue
                # Tentar selecionar por texto visível
                el.select_option(label=pension_label, timeout=5_000)
                self._log.debug(f"Pensão selecionada via <select> nativo: '{sel}'")
                return
            except Exception:
                # Tentar por valor parcial
                try:
                    el.select_option(
                        label=re.compile(
                            "Pequeno.Almo" if pension == "BB" else "^Alojamento$",
                            re.IGNORECASE
                        ),
                        timeout=3_000,
                    )
                    return
                except Exception:
                    continue

        # Dropdown custom (não é <select> nativo) — clicar para abrir e depois selecionar opção
        dropdown_selectors = [
            "[class*='pension'] [class*='dropdown']",
            "[class*='meal'] [class*='dropdown']",
            "[class*='regime'] [class*='select']",
            "div[class*='select']:has-text('Pensão')",
            "div[class*='select']:has-text('Regime')",
        ]

        for sel in dropdown_selectors:
            try:
                self._page.locator(sel).first.click(timeout=3_000)
                time.sleep(0.3)
                # Procurar a opção pelo texto
                keyword = "Pequeno" if pension == "BB" else "^Alojamento"
                option = self._page.locator(
                    f"[role='option']:has-text('{keyword}'), "
                    f"li:has-text('{keyword}'), "
                    f".dropdown-item:has-text('{keyword}')"
                ).first
                option.click(timeout=3_000)
                self._log.debug(f"Pensão selecionada via dropdown custom: '{sel}'")
                return
            except Exception:
                continue

        self._log.warning(
            f"Não foi possível selecionar o regime de pensão '{pension}'. "
            "Continuando com o valor predefinido da página."
        )

    def _select_tipologia(self, tipologia: str) -> None:
        """
        Seleciona uma tipologia específica no dropdown 'Tipo de Alojamento'.
        tipologia: "T0", "T1" ou "T2"
        """
        self._log.debug(f"A selecionar tipologia: {tipologia}")

        # Tentar <select> nativo
        select_selectors = [
            "select[name*='tipologia']",
            "select[name*='type']",
            "select[name*='roomtype']",
            "select[aria-label*='Tipo']",
            "select[aria-label*='Alojamento']",
        ]

        for sel in select_selectors:
            try:
                el = self._page.locator(sel).first
                if el.count() == 0:
                    continue
                el.select_option(label=re.compile(tipologia, re.IGNORECASE), timeout=5_000)
                self._log.debug(f"Tipologia '{tipologia}' selecionada via '{sel}'")
                return
            except Exception:
                continue

        # Dropdown custom
        dropdown_selectors = [
            "[class*='tipologia'] [class*='dropdown']",
            "[class*='roomtype'] [class*='select']",
            "div[class*='select']:has-text('Tipo de Alojamento')",
        ]

        for sel in dropdown_selectors:
            try:
                self._page.locator(sel).first.click(timeout=3_000)
                time.sleep(0.3)
                option = self._page.locator(
                    f"[role='option']:has-text('{tipologia}'), "
                    f"li:has-text('{tipologia}')"
                ).first
                option.click(timeout=3_000)
                self._log.debug(f"Tipologia selecionada via dropdown custom: '{tipologia}'")
                return
            except Exception:
                continue

        self._log.warning(
            f"Não foi possível selecionar a tipologia '{tipologia}'. "
            "A pesquisa será feita para todas as tipologias."
        )

    def _click_search(self) -> None:
        """
        Clica no botão 'Procurar' e aguarda o carregamento dos resultados.
        Raises PlaywrightTimeoutError se a tabela não aparecer dentro do timeout.
        """
        try:
            from paraiso_bot import config as cfg
            timeout_ms = cfg.PMS_SEARCH_TIMEOUT * 1000
        except ImportError:
            timeout_ms = 15_000

        self._log.debug("A clicar no botão 'Procurar'...")

        # Seletores para o botão Procurar
        search_btn_selectors = [
            "button:has-text('Procurar')",
            "button:has-text('Pesquisar')",
            "button:has-text('Search')",
            "button[type='submit']",
            "[class*='search-btn']",
            "[class*='btn-search']",
            "input[type='submit'][value*='Procurar']",
        ]

        clicked = False
        for sel in search_btn_selectors:
            try:
                btn = self._page.locator(sel).first
                if btn.count() == 0:
                    continue
                btn.click(timeout=5_000)
                clicked = True
                self._log.debug(f"Botão Procurar clicado via seletor: '{sel}'")
                break
            except Exception:
                continue

        if not clicked:
            # Último recurso: procurar qualquer botão primário na zona de filtros
            try:
                self._page.locator("form button[class*='primary'], .btn-primary").first.click(timeout=5_000)
                clicked = True
                self._log.debug("Botão Procurar clicado via botão primário genérico.")
            except Exception:
                raise PlaywrightTimeoutError("Botão Procurar não encontrado.")

        # Aguardar que os resultados apareçam (tabela com preços)
        result_selectors = [
            "table",
            "[class*='results']",
            "[class*='rates-grid']",
            "[class*='availability']",
            "td",
        ]

        for sel in result_selectors:
            try:
                self._page.wait_for_selector(sel, timeout=timeout_ms)
                self._log.debug(f"Resultados detetados via seletor: '{sel}'")
                # Espera adicional para garantir que todos os dados foram renderizados
                time.sleep(1)
                return
            except PlaywrightTimeoutError:
                continue

        raise PlaywrightTimeoutError(
            f"A tabela de resultados não apareceu dentro de {timeout_ms}ms."
        )

    # -----------------------------------------------------------------------
    # Extração de resultados da grelha
    # -----------------------------------------------------------------------

    def _extract_results(self, nights: int) -> list[TipologiaResult]:
        """
        Extrai os dados da grelha de resultados do Newhotel.

        A grelha tem:
          - Colunas por tipologia (T0, T1, T2) com subcolunas 'Preço' e 'Disponível'
          - Linhas de data (uma por noite)
          - Linha final de totais

        Retorna lista de TipologiaResult. Levanta _NoResultsError se não extrair nada.
        """
        self._log.debug("A extrair resultados da grelha do Newhotel...")

        try:
            # Aguardar que a tabela esteja visível
            self._page.wait_for_selector("table, [class*='grid'], [class*='results']", timeout=10_000)
        except PlaywrightTimeoutError:
            raise _NoResultsError("Tabela de resultados não encontrada.")

        # Usar JavaScript para extrair toda a informação da tabela de forma robusta
        # (mais fiável do que seletores CSS em tabelas com headers complexos)
        raw_data = self._page.evaluate("""
        () => {
            // Tentar encontrar a tabela de resultados
            const tables = document.querySelectorAll('table');
            if (tables.length === 0) return null;

            // Procurar a tabela que contém dados de tipologias (T0, T1, T2)
            let targetTable = null;
            for (const t of tables) {
                const text = t.innerText || t.textContent || '';
                if (text.includes('T0') || text.includes('T1') || text.includes('T2')
                    || text.includes('Preço') || text.includes('Price')) {
                    targetTable = t;
                    break;
                }
            }
            if (!targetTable) {
                // Fallback: usar a primeira tabela grande
                targetTable = tables[0];
            }

            // Extrair todos os dados da tabela como matriz texto + estilos de fundo
            const rows = targetTable.querySelectorAll('tr');
            const result = [];
            for (const row of rows) {
                const cells = row.querySelectorAll('td, th');
                const rowData = [];
                for (const cell of cells) {
                    const style = window.getComputedStyle(cell);
                    const bg = style.backgroundColor || '';
                    rowData.push({
                        text: (cell.innerText || cell.textContent || '').trim(),
                        bg: bg,
                        colspan: parseInt(cell.getAttribute('colspan') || '1', 10),
                        isHeader: cell.tagName === 'TH',
                    });
                }
                result.push(rowData);
            }
            return result;
        }
        """)

        if not raw_data:
            raise _NoResultsError("Não foi possível extrair dados da tabela.")

        self._log.debug(f"Tabela extraída: {len(raw_data)} linhas")
        return self._parse_table_data(raw_data, nights)

    def _parse_table_data(self, raw_data: list, nights: int) -> list[TipologiaResult]:
        """
        Analisa a matriz de dados brutos da tabela e constrói a lista de TipologiaResult.

        Estratégia:
          1. Encontrar a linha de cabeçalho com os nomes das tipologias (T0, T1, T2)
          2. Mapear índices de colunas para cada tipologia (Preço e Disponível)
          3. Para cada linha de data, extrair preço e disponibilidade
          4. A última linha com valores numéricos é o total
        """
        if not raw_data:
            return []

        # ---- Passo 1: Identificar colunas de tipologias ----
        tipologia_columns: dict[str, dict] = {}  # {"T0": {"price_col": int, "avail_col": int}}

        header_row_idx = -1
        for i, row in enumerate(raw_data):
            row_text = " ".join(cell["text"] for cell in row)
            if any(code in row_text for code in ["T0", "T1", "T2"]):
                header_row_idx = i
                break

        if header_row_idx == -1:
            self._log.warning("Não foi possível identificar a linha de cabeçalho com tipologias.")
            return []

        # Reconstruir índice de colunas (com colspan)
        header_row = raw_data[header_row_idx]
        col_index = 0
        tipologia_col_start: dict[str, int] = {}

        for cell in header_row:
            for code in ["T0", "T1", "T2"]:
                if code in cell["text"].upper():
                    tipologia_col_start[code] = col_index
            col_index += cell.get("colspan", 1)

        # ---- Passo 2: Encontrar a sub-linha de Preço/Disponível ----
        # (a linha seguinte ao cabeçalho principal normalmente tem "Preço" e "Disponível")
        subheader_row_idx = header_row_idx + 1
        if subheader_row_idx < len(raw_data):
            subheader = raw_data[subheader_row_idx]
            sub_col_idx = 0
            price_col_for_tipologia: dict[str, int] = {}
            avail_col_for_tipologia: dict[str, int] = {}

            for code, start_col in tipologia_col_start.items():
                # As duas colunas após start_col são Preço e Disponível
                price_col_for_tipologia[code] = start_col
                avail_col_for_tipologia[code] = start_col + 1

        # ---- Passo 3: Extrair linhas de dados (datas) ----
        nightly_rates_by_tipologia: dict[str, list[NightlyRate]] = {
            code: [] for code in tipologia_col_start
        }
        total_prices: dict[str, float] = {code: 0.0 for code in tipologia_col_start}

        # Data de início para atribuir às linhas
        # (extraída da primeira célula da linha, ou calculada sequencialmente)
        data_rows = raw_data[subheader_row_idx + 1:]

        current_date = None  # será preenchido quando encontrarmos a data

        for row in data_rows:
            if not row:
                continue

            row_texts = [cell["text"] for cell in row]
            row_text_joined = " ".join(row_texts)

            # Verificar se é linha de total (última linha)
            is_total_row = (
                "total" in row_text_joined.lower()
                or "total" in (row[0]["text"]).lower()
                or (len(nightly_rates_by_tipologia.get(
                    next(iter(tipologia_col_start), "T0"), []
                )) >= nights and len(row_texts) > 1)
            )

            # Tentar extrair data da primeira célula
            date_match = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})", row_texts[0] if row_texts else "")
            if date_match:
                d, m, y = date_match.groups()
                y = int(y)
                if y < 100:
                    y += 2000
                try:
                    current_date = date(y, int(m), int(d))
                except ValueError:
                    current_date = None

            # Extrair valores para cada tipologia
            for code, start_col in tipologia_col_start.items():
                price_col = price_col_for_tipologia.get(code, start_col)
                avail_col = avail_col_for_tipologia.get(code, start_col + 1)

                # Verificar limites
                if price_col >= len(row) or avail_col >= len(row):
                    continue

                price_text = row[price_col]["text"] if price_col < len(row) else ""
                avail_text = row[avail_col]["text"] if avail_col < len(row) else ""
                cell_bg = row[price_col]["bg"] if price_col < len(row) else ""

                price_val = self._parse_price(price_text)
                avail_val = self._parse_availability(avail_text, cell_bg)

                if is_total_row:
                    if price_val is not None:
                        total_prices[code] = price_val
                elif price_val is not None and current_date is not None:
                    nightly_rates_by_tipologia[code].append(
                        NightlyRate(
                            date=current_date,
                            price=price_val,
                            available=avail_val if avail_val is not None else 0,
                        )
                    )

        # ---- Passo 4: Construir TipologiaResult para cada código ----
        results: list[TipologiaResult] = []

        for code in tipologia_col_start:
            rates = nightly_rates_by_tipologia[code]
            if not rates:
                self._log.debug(f"Tipologia {code}: sem dados de preços — ignorada.")
                continue

            total = total_prices.get(code) or sum(r.price for r in rates)
            is_available = all(r.is_available for r in rates)

            results.append(TipologiaResult(
                code=code,
                nightly_rates=rates,
                total_price=round(total, 2),
                is_available=is_available,
            ))
            self._log.debug(
                f"Tipologia {code}: {len(rates)} noites, total={total:.2f}€, "
                f"disponível={is_available}"
            )

        if not results:
            raise _NoResultsError("Nenhuma tipologia extraída da tabela.")

        return results

    @staticmethod
    def _parse_price(text: str) -> Optional[float]:
        """
        Converte texto de preço para float.
        Trata formato europeu: "1.234,56 €" → 1234.56
        """
        if not text:
            return None
        # Remover símbolo de moeda e espaços
        cleaned = re.sub(r"[€$£\s]", "", text)
        # Remover separadores de milhar (ponto no formato europeu)
        # e substituir vírgula decimal por ponto
        if "," in cleaned and "." in cleaned:
            # Formato: 1.234,56 → remover pontos de milhar, substituir vírgula
            cleaned = cleaned.replace(".", "").replace(",", ".")
        elif "," in cleaned:
            # Só vírgula: provavelmente decimal (234,56)
            cleaned = cleaned.replace(",", ".")

        try:
            val = float(cleaned)
            return val if val >= 0 else None
        except ValueError:
            return None

    @staticmethod
    def _parse_availability(text: str, background_color: str = "") -> Optional[int]:
        """
        Determina a disponibilidade a partir do texto e/ou cor de fundo da célula.
        Células com fundo alaranjado/bege = indisponível (0).
        """
        # Verificar cor de fundo (alaranjado = indisponível)
        # As cores do Newhotel para indisponível são tipicamente rgb(255, 220, 180) ou similares
        if background_color:
            # Extrair componentes RGB
            rgb_match = re.search(r"rgb\((\d+),\s*(\d+),\s*(\d+)\)", background_color)
            if rgb_match:
                r, g, b = int(rgb_match.group(1)), int(rgb_match.group(2)), int(rgb_match.group(3))
                # Alaranjado: R alto, G médio, B baixo
                if r > 200 and g > 150 and b < 150:
                    return 0

        # Tentar extrair número do texto
        if not text:
            return None
        nums = re.findall(r"\d+", text)
        if nums:
            return int(nums[0])

        # Ícones ou texto que indicam disponibilidade
        text_lower = text.lower()
        if any(x in text_lower for x in ["✓", "ok", "sim", "yes", "available"]):
            return 1
        if any(x in text_lower for x in ["✗", "não", "no", "esgotado", "unavailable"]):
            return 0

        return None

    # -----------------------------------------------------------------------
    # Screenshots de auditoria
    # -----------------------------------------------------------------------

    def _take_audit_screenshot(self, prefix: str = "query") -> str:
        """
        Guarda um screenshot da página atual em audit_screenshots_dir.
        Nome: audit_YYYYMMDD_HHMMSS_{prefix}.png
        Retorna o caminho do ficheiro guardado.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"audit_{timestamp}_{prefix}.png"
        filepath = self._audit_dir / filename

        try:
            # Screenshot de página completa (não apenas viewport)
            self._page.screenshot(path=str(filepath), full_page=True)
            self._log.debug(f"Screenshot de auditoria guardado: {filepath}")
            return str(filepath)
        except Exception as exc:
            self._log.warning(f"Não foi possível guardar screenshot: {exc}")
            return ""

    def _cleanup_old_screenshots(self) -> None:
        """
        Remove screenshots com mais de 7 dias da pasta de auditoria.
        Executado na inicialização do serviço.
        """
        try:
            from paraiso_bot import config as cfg
            retention_days = cfg.DATA_RETENTION_DAYS
        except ImportError:
            retention_days = 7

        cutoff = datetime.now() - timedelta(days=retention_days)
        removed = 0
        for f in self._audit_dir.glob("audit_*.png"):
            try:
                if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                    f.unlink()
                    removed += 1
            except Exception:
                pass

        if removed > 0:
            self._log.debug(f"Limpeza de screenshots: {removed} ficheiro(s) removido(s).")

    # -----------------------------------------------------------------------
    # Lógica de datas alternativas
    # -----------------------------------------------------------------------

    def _compute_alternative_date(
        self, base_checkin: date, nights: int, attempt: int
    ) -> Optional[date]:
        """
        Calcula a data de checkin alternativa para a pesquisa de fallback.

        Regras:
          - Se o período original inclui fim de semana → próximo sábado
          - Se é semana → próximo dia útil (avança 1 dia, máx. 7 tentativas)

        attempt: 1 ou 2 (para calcular a segunda alternativa a partir da primeira)
        """
        if self._is_weekend_period(base_checkin, nights):
            alt = self._next_weekend(base_checkin + timedelta(days=1))
            self._log.debug(f"Alternativa {attempt}: próximo fim de semana → {alt}")
            return alt
        else:
            alt = self._next_available_weekday(base_checkin + timedelta(days=1))
            self._log.debug(f"Alternativa {attempt}: próximo dia útil → {alt}")
            return alt

    def _is_weekend_period(self, checkin: date, nights: int) -> bool:
        """
        Verifica se o período pedido inclui pelo menos um dia de fim de semana
        (sábado = 5, domingo = 6 no weekday() do Python).
        """
        for i in range(nights):
            d = checkin + timedelta(days=i)
            if d.weekday() >= 5:  # 5=sábado, 6=domingo
                return True
        return False

    def _next_weekend(self, from_date: date) -> date:
        """
        Retorna o próximo sábado a partir de from_date (inclusive).
        """
        days_until_saturday = (5 - from_date.weekday()) % 7
        if days_until_saturday == 0:
            days_until_saturday = 7  # Evitar retornar a mesma data
        return from_date + timedelta(days=days_until_saturday)

    def _next_available_weekday(self, from_date: date) -> date:
        """
        Retorna o próximo dia útil (segunda a sexta) a partir de from_date.
        Avança até 7 dias; se não encontrar, retorna from_date + 7.
        """
        for i in range(7):
            candidate = from_date + timedelta(days=i)
            if candidate.weekday() < 5:  # 0=segunda, ..., 4=sexta
                return candidate
        return from_date + timedelta(days=7)


# ---------------------------------------------------------------------------
# Exceções internas do módulo (não escapam para fora da classe)
# ---------------------------------------------------------------------------

class _LoginRequiredError(Exception):
    """Lançada quando o Newhotel exige login e não foi possível autenticar."""
    pass


class _NetworkError(Exception):
    """Lançada quando há falha de rede ao aceder ao Newhotel."""
    pass


class _NoResultsError(Exception):
    """Lançada quando a pesquisa não retorna nenhuma tipologia."""
    pass
