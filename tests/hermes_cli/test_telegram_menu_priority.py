"""Telegram menu: `priority` deve valer para a lista COMBINADA, não só para os built-ins.

Contexto do problema (comportamento atual, indesejado)
------------------------------------------------------
`telegram_menu_commands()` monta o menu em três camadas:

    core_commands = _prioritize_telegram_menu_commands(telegram_bot_commands())
    all_commands  = list(core_commands)                    # ~53 built-ins, SEMPRE
    remaining     = max(0, max_commands - len(all_commands))
    entries, hidden = _collect_gateway_skill_entries(max_slots=remaining, ...)

Consequências que estes testes travam:

1. `priority` só reordena os built-ins entre si. Listar uma SKILL em `priority`
   é no-op — ela continua disputando as vagas que sobrarem, em ordem alfabética.
2. Como os built-ins entram sempre e são ~53, qualquer `max_commands` abaixo
   disso zera as vagas de skill. Um menu curto e curado é impossível hoje:
   quanto menor o teto, MENOS comando do usuário aparece.

Comportamento desejado
----------------------
`priority` é a curadoria do usuário e vale sobre tudo que pode entrar no menu —
built-in, plugin ou skill. Nada listado em `priority` pode ser descartado em
favor de algo que não está listado. O resto preenche as vagas restantes na
ordem atual (built-ins, depois plugins, depois skills alfabéticas).

Estes testes falham no comportamento atual e passam no desejado.
"""

from unittest.mock import patch

import pytest

from hermes_cli.commands import telegram_bot_commands, telegram_menu_commands


def _fake_skills(skills_dir, *names):
    """Skills mínimas no formato que ``get_skill_commands`` devolve.

    ``_collect_gateway_skill_entries`` filtra por prefixo de caminho contra
    ``SKILLS_DIR.resolve()`` — os paths precisam ficar DENTRO do diretório
    patcheado, senão a skill é descartada silenciosamente e o teste vira
    falso-negativo.
    """
    base = str(skills_dir.resolve())
    return {
        f"/{n}": {
            "name": n,
            "description": f"Skill {n}",
            "skill_md_path": f"{base}/{n}/SKILL.md",
            "skill_dir": f"{base}/{n}",
        }
        for n in names
    }


def _write_menu_config(tmp_path, priority, mode="replace", max_commands=None):
    cap = f"        max_commands: {max_commands}\n" if max_commands else ""
    prio = "".join(f"          - {p}\n" for p in priority)
    (tmp_path / "config.yaml").write_text(
        "platforms:\n"
        "  telegram:\n"
        "    extra:\n"
        "      command_menu:\n"
        f"{cap}"
        f"        priority_mode: {mode}\n"
        "        priority:\n"
        f"{prio}"
    )


@pytest.fixture
def menu(tmp_path, monkeypatch):
    """Monta o menu com skills falsas e HERMES_HOME isolado."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(exist_ok=True)

    def _build(skill_names, max_commands):
        with (
            patch("agent.skill_commands.get_skill_commands",
                  return_value=_fake_skills(skills_dir, *skill_names)),
            patch("tools.skills_tool.SKILLS_DIR", skills_dir),
        ):
            return telegram_menu_commands(max_commands=max_commands)

    return _build


class TestPriorityAppliesToSkills:
    """O núcleo do pedido: skill listada em priority tem que entrar."""

    def test_skill_in_priority_enters_menu_even_when_core_fills_the_cap(
        self, tmp_path, menu
    ):
        """Com teto menor que o nº de built-ins, a skill curada ainda entra.

        Hoje ``remaining_slots = max(0, 10 - 53) = 0`` e nenhuma skill entra —
        ou seja, apertar o teto REMOVE justamente os comandos do usuário.
        """
        _write_menu_config(tmp_path, ["ideia", "infra", "status"])

        names = [n for n, _ in menu(["ideia", "infra"], max_commands=10)[0]]

        assert "ideia" in names, "skill curada sumiu do menu com teto apertado"
        assert "infra" in names

    def test_curated_short_menu_contains_only_priority_entries(self, tmp_path, menu):
        """Teto igual ao tamanho da lista curada ⇒ menu é exatamente ela."""
        curated = ["status", "ideia", "infra", "new", "model"]
        _write_menu_config(tmp_path, curated)

        names = [n for n, _ in menu(["ideia", "infra"], max_commands=len(curated))[0]]

        assert names == curated

    def test_priority_order_is_preserved_across_tiers(self, tmp_path, menu):
        """A ordem configurada vale mesmo intercalando skill e built-in."""
        _write_menu_config(tmp_path, ["ideia", "status", "infra", "new"])

        names = [n for n, _ in menu(["ideia", "infra"], max_commands=20)[0]]

        assert names[:4] == ["ideia", "status", "infra", "new"]

    def test_non_priority_commands_fill_remaining_slots(self, tmp_path, menu):
        """Depois da lista curada, o resto entra até o teto — built-ins primeiro.

        Não se afirma QUAL built-in entra: com ``priority_mode: replace`` a lista
        padrão do Hermes é descartada de propósito, então a ordem do que sobra é
        a do registro. O que a especificação exige é que as vagas restantes sejam
        preenchidas, e que built-in tenha precedência sobre skill.
        """
        _write_menu_config(tmp_path, ["ideia"])

        result, _hidden = menu(["ideia", "infra"], max_commands=25)
        names = [n for n, _ in result]
        core_names = {n for n, _ in telegram_bot_commands()}

        assert names[0] == "ideia"
        assert len(names) == 25, "vagas restantes ficaram vazias"
        assert all(n in core_names for n in names[1:]), (
            "com 53 built-ins disponíveis, as 24 vagas seguintes têm de ser core "
            f"— vieram: {[n for n in names[1:] if n not in core_names]}"
        )

    def test_hidden_count_reflects_everything_dropped(self, tmp_path, menu):
        """`hidden` tem que contar built-in podado, não só skill."""
        _write_menu_config(tmp_path, ["ideia", "status"])

        result, hidden = menu(["ideia", "infra"], max_commands=5)
        total_available = len(telegram_bot_commands()) + 2  # built-ins + 2 skills

        assert len(result) == 5
        assert hidden == total_available - 5


class TestBackwardCompatibility:
    """O que já funcionava não pode quebrar."""

    def test_without_priority_config_core_still_comes_first(self, tmp_path, menu):
        """Sem curadoria, mantém o comportamento atual: built-ins primeiro."""
        monkey_names = [n for n, _ in menu(["zzz_skill"], max_commands=100)[0]]

        assert "help" in monkey_names
        core_names = {n for n, _ in telegram_bot_commands()}
        assert monkey_names[0] in core_names

    def test_skills_still_fill_leftover_slots_when_cap_is_generous(
        self, tmp_path, menu
    ):
        """Teto folgado ⇒ skills entram mesmo sem estar em priority."""
        names = [n for n, _ in menu(["ideia", "infra"], max_commands=100)[0]]

        assert "ideia" in names
        assert "infra" in names

    def test_cap_is_never_exceeded(self, tmp_path, menu):
        """Invariante da Bot API: nunca passar do teto pedido."""
        _write_menu_config(tmp_path, ["ideia", "infra", "status"])

        for cap in (1, 3, 10, 60, 100):
            result, _ = menu(["ideia", "infra"], max_commands=cap)
            assert len(result) <= cap, f"estourou o teto em max_commands={cap}"

    def test_priority_naming_unknown_command_is_ignored(self, tmp_path, menu):
        """Nome inexistente em priority não pode quebrar nem criar entrada fantasma."""
        _write_menu_config(tmp_path, ["comando_que_nao_existe", "ideia"])

        names = [n for n, _ in menu(["ideia"], max_commands=20)[0]]

        assert "comando_que_nao_existe" not in names
        assert names[0] == "ideia"
