"""Recon plans must stay read-only and inside walmart.com."""

from __future__ import annotations

import pytest

import recon_plans
import safety

ALL_URLS = [(name, url) for name, plan in recon_plans.PLANS.items() for url in plan.urls]


@pytest.mark.parametrize(("name", "url"), ALL_URLS)
def test_no_plan_visits_a_forbidden_destination(name, url):
    assert not safety.is_forbidden_url(url), f"plan {name} would navigate to {url}"
    safety.assert_navigable(url)


@pytest.mark.parametrize(("name", "url"), ALL_URLS)
def test_plans_stay_on_walmart_over_https(name, url):
    assert url.startswith("https://www.walmart.com"), f"plan {name} leaves walmart.com: {url}"


def test_every_plan_declares_what_it_is_trying_to_learn():
    for name, plan in recon_plans.PLANS.items():
        assert plan.questions, f"plan {name} does not say what it answers"
        assert plan.description, f"plan {name} has no description"


def test_plan_names_are_sorted_and_complete():
    assert recon_plans.plan_names() == sorted(recon_plans.PLANS)
    assert "observe" in recon_plans.PLANS
