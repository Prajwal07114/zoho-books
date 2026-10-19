"""Opening Balance Service - Initial Migration Batches & Equity Offsets (JP-14)."""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounting import Account
from app.models.identity import User
from app.models.opening_balance import OpeningBalanceBatch
from app.schemas.opening_balance import OpeningBalanceCreate
from app.services.posting_gateway import (
    PostingGateway,
    PostingLine,
    build_jp14_opening_balance,
)


def quantize_money(amount: Decimal) -> Decimal:
    """Format and round monetary decimal to 4 standard fractional places."""
    return amount.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


class OpeningBalanceService:
    @staticmethod
    async def record_opening_balances(
        db: AsyncSession,
        organization_id: uuid.UUID,
        payload: OpeningBalanceCreate,
        current_user: User,
    ) -> OpeningBalanceBatch:
        """Record opening balance batch with automatic Opening Balance Equity (3030) balancing."""
        # 1. Check for existing active opening balance batch
        exist_stmt = select(OpeningBalanceBatch.id).where(
            OpeningBalanceBatch.organization_id == organization_id,
            OpeningBalanceBatch.status == "posted",
        )
        exist_res = await db.execute(exist_stmt)
        if exist_res.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An active opening balance batch already exists for this organization",
            )

        # 2. Validate line accounts and compute totals
        posting_lines: List[PostingLine] = []
        total_debits = Decimal("0.0000")
        total_credits = Decimal("0.0000")

        account_ids = {line.account_id for line in payload.lines}
        acc_stmt = select(Account).where(
            Account.organization_id == organization_id,
            Account.id.in_(account_ids),
            Account.status == "active",
            Account.is_postable == True,
        )
        acc_res = await db.execute(acc_stmt)
        accounts_by_id = {acc.id: acc for acc in acc_res.scalars().all()}

        for line_in in payload.lines:
            if line_in.account_id not in accounts_by_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Account {line_in.account_id} not found, inactive, or not postable",
                )

            deb = quantize_money(line_in.debit)
            cred = quantize_money(line_in.credit)

            if deb == 0 and cred == 0:
                continue
            if deb > 0 and cred > 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Line for account {accounts_by_id[line_in.account_id].name} cannot have both debit and credit amounts",
                )

            posting_lines.append(
                PostingLine(
                    account_id=line_in.account_id,
                    debit=deb,
                    credit=cred,
                    description=line_in.description or "Opening balance",
                )
            )
            total_debits += deb
            total_credits += cred

        if not posting_lines:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one non-zero opening balance line is required",
            )

        # 3. Handle Equity Offset if entries do not balance
        if total_debits != total_credits:
            diff = abs(total_debits - total_credits)
            eq_stmt = select(Account.id).where(
                Account.organization_id == organization_id,
                Account.system_key == "opening_balance_equity",
            )
            eq_res = await db.execute(eq_stmt)
            equity_acc_id = eq_res.scalar_one_or_none()
            if not equity_acc_id:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Opening Balance Equity account (system key: opening_balance_equity) not found",
                )

            if total_debits > total_credits:
                # Need credit to balance
                posting_lines.append(
                    PostingLine(
                        account_id=equity_acc_id,
                        debit=Decimal("0.0000"),
                        credit=diff,
                        description="Opening balance equity offset",
                    )
                )
                total_credits += diff
            else:
                # Need debit to balance
                posting_lines.append(
                    PostingLine(
                        account_id=equity_acc_id,
                        debit=diff,
                        credit=Decimal("0.0000"),
                        description="Opening balance equity offset",
                    )
                )
                total_debits += diff

        # 4. Post JP-14 Journal
        batch_id = uuid.uuid4()
        jp14_req = await build_jp14_opening_balance(
            organization_id=organization_id,
            batch_id=batch_id,
            accounting_date=payload.conversion_date,
            lines=posting_lines,
            created_by=current_user.id,
            memo=payload.notes or "Opening balance conversion",
        )
        journal = await PostingGateway.post_journal(db, jp14_req)

        # 5. Create Batch Record
        batch = OpeningBalanceBatch(
            id=batch_id,
            organization_id=organization_id,
            conversion_date=payload.conversion_date,
            status="posted",
            journal_id=journal.id,
            notes=payload.notes,
            created_by=current_user.id,
        )
        db.add(batch)

        await db.flush()
        return batch

    @staticmethod
    async def get_active_batch(
        db: AsyncSession,
        organization_id: uuid.UUID,
    ) -> Optional[OpeningBalanceBatch]:
        """Fetch the posted opening balance batch for the organization."""
        stmt = select(OpeningBalanceBatch).where(
            OpeningBalanceBatch.organization_id == organization_id,
            OpeningBalanceBatch.status == "posted",
        )
        res = await db.execute(stmt)
        return res.scalar_one_or_none()
