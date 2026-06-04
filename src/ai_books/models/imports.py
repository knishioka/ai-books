"""CSV 取込の結果サマリ — the machine-readable outcome of ``import_transactions_csv``.

The import tool (#14) turns a bank/CC statement into *draft* 仕訳 and returns this
summary so a calling agent can reason about the run without re-querying: how many rows
were seen, how many became new drafts, how many were skipped as already-imported
(二重取込検知), and how many fell back to a suspense 科目 because no counter-account
could be inferred (相手科目未確定 → 後で振替). ``entry_ids`` lists the drafts created
this run so the caller can post (#13) or review them.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from .base import DomainModel


class ImportSummary(DomainModel):
    """Counts (and created ids) from one CSV import run."""

    total_rows: int = Field(ge=0)  # CSV の取込対象行数 (ヘッダ除く)
    imported: int = Field(ge=0)  # 新規作成された draft 仕訳の数
    duplicates: int = Field(ge=0)  # 既取込として skip した行数
    unassigned: int = Field(ge=0)  # 相手科目未確定で suspense に退避した仕訳の数
    entry_ids: list[int] = Field(default_factory=list)  # 作成した draft の id

    @model_validator(mode="after")
    def _check_totals(self) -> ImportSummary:
        if self.imported + self.duplicates != self.total_rows:
            raise ValueError(
                f"imported ({self.imported}) + duplicates ({self.duplicates}) "
                f"must equal total_rows ({self.total_rows})"
            )
        if self.unassigned > self.imported:
            raise ValueError(
                f"unassigned ({self.unassigned}) cannot exceed imported ({self.imported})"
            )
        if len(self.entry_ids) != self.imported:
            raise ValueError(
                f"entry_ids has {len(self.entry_ids)} ids but imported is {self.imported}"
            )
        return self
