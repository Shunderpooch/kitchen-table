"""SQLAlchemy models for the expenses pipeline."""

from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class Category(Base):
    """Main expense categories (e.g., 'Food and Drink - Dining Out')."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("categories.id"), nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)  # Hex color

    # Relationships
    parent: Mapped[Optional["Category"]] = relationship(
        "Category", remote_side=[id], back_populates="children"
    )
    children: Mapped[List["Category"]] = relationship("Category", back_populates="parent")
    subtypes: Mapped[List["Subtype"]] = relationship("Subtype", back_populates="category")
    expenses: Mapped[List["Expense"]] = relationship("Expense", back_populates="category_rel")

    def __repr__(self) -> str:
        return f"<Category(id={self.id}, name='{self.name}')>"


class Subtype(Base):
    """Expense subtypes within a category (e.g., 'Cafe', 'Dinner')."""

    __tablename__ = "subtypes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    keywords: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Comma-separated

    # Relationships
    category: Mapped["Category"] = relationship("Category", back_populates="subtypes")
    expenses: Mapped[List["Expense"]] = relationship("Expense", back_populates="subtype_rel")

    def __repr__(self) -> str:
        return f"<Subtype(id={self.id}, name='{self.name}', category_id={self.category_id})>"


class SourceFile(Base):
    """Track imported files."""

    __tablename__ = "source_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # csv, xlsx, pdf, image
    row_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending")  # pending, processing, completed, error
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    expenses: Mapped[List["Expense"]] = relationship("Expense", back_populates="source_file")

    def __repr__(self) -> str:
        return f"<SourceFile(id={self.id}, filename='{self.filename}', status='{self.status}')>"


class StatementSource(Base):
    """Track statement sources and freshness (per card/account)."""

    __tablename__ = "statement_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)  # "<member> <card>", e.g. "Alex Amex"
    source_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # credit_card, bank_account, manual
    last_import_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expected_frequency_days: Mapped[int] = mapped_column(Integer, default=30)

    def __repr__(self) -> str:
        return f"<StatementSource(id={self.id}, name='{self.name}')>"

    @property
    def is_stale(self) -> bool:
        """Check if this statement source is stale."""
        if self.last_import_date is None:
            return True
        days_since_update = (date.today() - self.last_import_date).days
        return days_since_update > self.expected_frequency_days


class Expense(Base):
    """Core expenses table - mirrors the CSV structure."""

    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    type: Mapped[str] = mapped_column(String(255), nullable=False)  # Main category name
    subtype: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    merchant: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    original_input_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    split: Mapped[Decimal] = mapped_column(Numeric(3, 2), default=Decimal("1.0"))
    # Each household member's share (household.py: member A, member B).
    member_b_spend: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    member_a_spend: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    spender: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # a household member's name
    card: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    log_to_work: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    tag: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    link: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    track_with_friends: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Foreign keys
    source_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("source_files.id"), nullable=True)
    category_id: Mapped[Optional[int]] = mapped_column(ForeignKey("categories.id"), nullable=True)
    subtype_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subtypes.id"), nullable=True)

    # Metadata
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_validation: Mapped[bool] = mapped_column(Boolean, default=False)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # For duplicate detection
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    # Relationships
    source_file: Mapped[Optional["SourceFile"]] = relationship("SourceFile", back_populates="expenses")
    category_rel: Mapped[Optional["Category"]] = relationship("Category", back_populates="expenses")
    subtype_rel: Mapped[Optional["Subtype"]] = relationship("Subtype", back_populates="expenses")
    validation_item: Mapped[Optional["ValidationQueueItem"]] = relationship(
        "ValidationQueueItem", back_populates="expense", uselist=False
    )

    def __repr__(self) -> str:
        return f"<Expense(id={self.id}, date={self.date}, merchant='{self.merchant}', amount={self.amount})>"


class Payment(Base):
    """Logged reimbursement/payment between the two household members."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    from_person: Mapped[str] = mapped_column(String(100), nullable=False)
    to_person: Mapped[str] = mapped_column(String(100), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    def __repr__(self) -> str:
        return (
            f"<Payment(id={self.id}, date={self.date}, from='{self.from_person}', "
            f"to='{self.to_person}', amount={self.amount})>"
        )


class MerchantMapping(Base):
    """Merchant patterns for auto-categorization."""

    __tablename__ = "merchant_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    merchant_pattern: Mapped[str] = mapped_column(String(500), nullable=False)  # regex or keyword
    category_id: Mapped[Optional[int]] = mapped_column(ForeignKey("categories.id"), nullable=True)
    subtype_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subtypes.id"), nullable=True)
    canonical_merchant: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    requires_validation: Mapped[bool] = mapped_column(Boolean, default=False)  # True for Amazon, eBay, etc.

    def __repr__(self) -> str:
        return f"<MerchantMapping(id={self.id}, pattern='{self.merchant_pattern}')>"


class ValidationQueueItem(Base):
    """Queue of transactions that need manual categorization."""

    __tablename__ = "validation_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    expense_id: Mapped[int] = mapped_column(ForeignKey("expenses.id"), nullable=False, unique=True)
    reason: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # vague_merchant, new_merchant, etc.
    suggested_category_id: Mapped[Optional[int]] = mapped_column(ForeignKey("categories.id"), nullable=True)
    suggested_subtype_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subtypes.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending")  # pending, validated, skipped
    validated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    expense: Mapped["Expense"] = relationship("Expense", back_populates="validation_item")

    def __repr__(self) -> str:
        return f"<ValidationQueueItem(id={self.id}, expense_id={self.expense_id}, status='{self.status}')>"


class ScannedDocument(Base):
    """A filed OneDrive document (receipt, manual, car paperwork) and what was read from it."""

    __tablename__ = "scanned_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String(1000), nullable=False, unique=True)  # relative to OneDrive
    folder: Mapped[str] = mapped_column(String(500), nullable=False)
    person: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # people/<key>, else None
    kind: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    doc_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)  # from the filename
    doc_time: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    merchant: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    file_modified: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Content hash: a renamed or moved file keeps its text without being read again.
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    ocr_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)  # read from the text
    ocr_total: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    text_source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # pdf_text / ocr / mixed
    indexed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    pages: Mapped[List["ScannedPage"]] = relationship(
        "ScannedPage", back_populates="document", cascade="all, delete-orphan",
        order_by="ScannedPage.page_number",
    )

    def __repr__(self) -> str:
        return f"<ScannedDocument(id={self.id}, path='{self.path}')>"


class LabelFinding(Base):
    """A possible mistake in how a document was named or filed, with its review."""

    __tablename__ = "label_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String(1000), nullable=False, index=True)  # relative to OneDrive
    check: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # what the name/folder says
    evidence: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # what the text or history says
    suggestion: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    strength: Mapped[float] = mapped_column(Numeric(4, 3), default=0)
    found_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # Visual review of the page image: label_wrong / text_wrong / both_wrong / unclear.
    reviewer_verdict: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    reviewer_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # The owner's call: fix / keep / later. Nothing is renamed without it.
    user_decision: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)


class ScannedPage(Base):
    """Text of one page of a ScannedDocument."""

    __tablename__ = "scanned_pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("scanned_documents.id"), nullable=False, index=True)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(String(30), nullable=False)  # pdf_text / ocr:<engine> / none

    document: Mapped["ScannedDocument"] = relationship("ScannedDocument", back_populates="pages")
