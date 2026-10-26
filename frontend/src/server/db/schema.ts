import { sqliteTable, text, integer, real, type AnySQLiteColumn } from "drizzle-orm/sqlite-core";
import { relations } from "drizzle-orm";

// Categories table
export const categories = sqliteTable("categories", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  name: text("name").notNull().unique(),
  parentId: integer("parent_id").references((): AnySQLiteColumn => categories.id),
  color: text("color"),
});

export const categoriesRelations = relations(categories, ({ one, many }) => ({
  parent: one(categories, {
    fields: [categories.parentId],
    references: [categories.id],
    relationName: "categoryHierarchy",
  }),
  children: many(categories, { relationName: "categoryHierarchy" }),
  subtypes: many(subtypes),
  expenses: many(expenses),
}));

// Subtypes table
export const subtypes = sqliteTable("subtypes", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  categoryId: integer("category_id")
    .notNull()
    .references(() => categories.id),
  name: text("name").notNull(),
  keywords: text("keywords"),
});

export const subtypesRelations = relations(subtypes, ({ one, many }) => ({
  category: one(categories, {
    fields: [subtypes.categoryId],
    references: [categories.id],
  }),
  expenses: many(expenses),
}));

// Source files table
export const sourceFiles = sqliteTable("source_files", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  filename: text("filename").notNull(),
  fileType: text("file_type"),
  rowCount: integer("row_count"),
  processedAt: integer("processed_at", { mode: "timestamp" }),
  status: text("status").default("pending"),
  errorMessage: text("error_message"),
});

export const sourceFilesRelations = relations(sourceFiles, ({ many }) => ({
  expenses: many(expenses),
}));

// Statement sources table
export const statementSources = sqliteTable("statement_sources", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  name: text("name").notNull().unique(),
  sourceType: text("source_type"),
  owner: text("owner"),
  lastImportDate: integer("last_import_date", { mode: "timestamp" }),
  expectedFrequencyDays: integer("expected_frequency_days").default(30),
});

export const payments = sqliteTable("payments", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  date: text("date").notNull(),
  fromPerson: text("from_person").notNull(),
  toPerson: text("to_person").notNull(),
  amount: real("amount").notNull(),
  note: text("note"),
  createdAt: integer("created_at", { mode: "timestamp" }).default(new Date()),
});

export const friends = sqliteTable("friends", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  name: text("name").notNull().unique(),
  createdAt: integer("created_at", { mode: "timestamp" }).default(new Date()),
});

// Expenses table
export const expenses = sqliteTable("expenses", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  date: text("date").notNull(), // Stored as "YYYY-MM-DD" strings by Python
  type: text("type").notNull(),
  subtype: text("subtype"),
  merchant: text("merchant"),
  originalInputName: text("original_input_name"),
  amount: real("amount").notNull(),
  split: real("split").default(1.0),
  memberBSpend: real("member_b_spend"),
  memberASpend: real("member_a_spend"),
  spender: text("spender"),
  card: text("card"),
  logToWork: integer("log_to_work", { mode: "boolean" }),
  tag: text("tag"),
  link: text("link"),
  trackWithFriends: text("track_with_friends"),
  sourceFileId: integer("source_file_id").references(() => sourceFiles.id),
  categoryId: integer("category_id").references(() => categories.id),
  subtypeId: integer("subtype_id").references(() => subtypes.id),
  isDuplicate: integer("is_duplicate", { mode: "boolean" }).default(false),
  needsValidation: integer("needs_validation", { mode: "boolean" }).default(false),
  isApproved: integer("is_approved", { mode: "boolean" }).default(false),
  approvedAt: integer("approved_at", { mode: "timestamp" }),
  contentHash: text("content_hash"),
  createdAt: integer("created_at", { mode: "timestamp" }).default(new Date()),
});

export const expenseFriendShares = sqliteTable("expense_friend_shares", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  expenseId: integer("expense_id")
    .notNull()
    .references(() => expenses.id),
  friendId: integer("friend_id")
    .notNull()
    .references(() => friends.id),
  amount: real("amount").notNull(),
});

export const expensesRelations = relations(expenses, ({ one }) => ({
  sourceFile: one(sourceFiles, {
    fields: [expenses.sourceFileId],
    references: [sourceFiles.id],
  }),
  categoryRel: one(categories, {
    fields: [expenses.categoryId],
    references: [categories.id],
  }),
  subtypeRel: one(subtypes, {
    fields: [expenses.subtypeId],
    references: [subtypes.id],
  }),
  validationItem: one(validationQueue, {
    fields: [expenses.id],
    references: [validationQueue.expenseId],
  }),
}));

export const friendsRelations = relations(friends, ({ many }) => ({
  shares: many(expenseFriendShares),
}));

export const expenseFriendSharesRelations = relations(expenseFriendShares, ({ one }) => ({
  expense: one(expenses, {
    fields: [expenseFriendShares.expenseId],
    references: [expenses.id],
  }),
  friend: one(friends, {
    fields: [expenseFriendShares.friendId],
    references: [friends.id],
  }),
}));

// Scanned documents: one row per filed OneDrive PDF, with the text read from it.
// Created and filled by the Python `scans index-text` / `scans apply` commands.
export const scannedDocuments = sqliteTable("scanned_documents", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  path: text("path").notNull().unique(), // relative to OneDrive, "/" separated
  folder: text("folder").notNull(),
  person: text("person"),
  kind: text("kind"),
  docDate: text("doc_date"),
  docTime: text("doc_time"),
  merchant: text("merchant"),
  pageCount: integer("page_count"),
  fileSize: integer("file_size"),
  sha256: text("sha256"),
  ocrDate: text("ocr_date"),
  ocrTotal: real("ocr_total"),
  textSource: text("text_source"),
});

export const scannedPages = sqliteTable("scanned_pages", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  documentId: integer("document_id")
    .notNull()
    .references(() => scannedDocuments.id),
  pageNumber: integer("page_number").notNull(),
  text: text("text").notNull(),
  source: text("source").notNull(),
});

// Possible filing mistakes found by `scans audit-labels`, with review and decision.
export const labelFindings = sqliteTable("label_findings", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  path: text("path").notNull(),
  check: text("check").notNull(),
  label: text("label"),
  evidence: text("evidence"),
  suggestion: text("suggestion"),
  strength: real("strength"),
  reviewerVerdict: text("reviewer_verdict"),
  reviewerNote: text("reviewer_note"),
  reviewedBy: text("reviewed_by"),
  userDecision: text("user_decision"),
});

// Which scanned receipt belongs to which expense (suggested, confirmed, rejected).
export const expenseDocuments = sqliteTable("expense_documents", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  expenseId: integer("expense_id")
    .notNull()
    .references(() => expenses.id),
  documentId: integer("document_id")
    .notNull()
    .references(() => scannedDocuments.id),
  score: real("score").notNull(),
  reasons: text("reasons"),
  method: text("method").notNull(),
  status: text("status").notNull(),
});

export const expenseDocumentsRelations = relations(expenseDocuments, ({ one }) => ({
  expense: one(expenses, {
    fields: [expenseDocuments.expenseId],
    references: [expenses.id],
  }),
  document: one(scannedDocuments, {
    fields: [expenseDocuments.documentId],
    references: [scannedDocuments.id],
  }),
}));

export const scannedDocumentsRelations = relations(scannedDocuments, ({ many }) => ({
  pages: many(scannedPages),
  expenseLinks: many(expenseDocuments),
}));

export const scannedPagesRelations = relations(scannedPages, ({ one }) => ({
  document: one(scannedDocuments, {
    fields: [scannedPages.documentId],
    references: [scannedDocuments.id],
  }),
}));

// Merchant mappings table
export const merchantMappings = sqliteTable("merchant_mappings", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  merchantPattern: text("merchant_pattern").notNull(),
  categoryId: integer("category_id").references(() => categories.id),
  subtypeId: integer("subtype_id").references(() => subtypes.id),
  canonicalMerchant: text("canonical_merchant"),
  requiresValidation: integer("requires_validation", { mode: "boolean" }).default(false),
});

// Validation queue table
export const validationQueue = sqliteTable("validation_queue", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  expenseId: integer("expense_id")
    .notNull()
    .unique()
    .references(() => expenses.id),
  reason: text("reason"),
  suggestedCategoryId: integer("suggested_category_id").references(() => categories.id),
  suggestedSubtypeId: integer("suggested_subtype_id").references(() => subtypes.id),
  status: text("status").default("pending"),
  validatedAt: integer("validated_at", { mode: "timestamp" }),
});

export const validationQueueRelations = relations(validationQueue, ({ one }) => ({
  expense: one(expenses, {
    fields: [validationQueue.expenseId],
    references: [expenses.id],
  }),
  suggestedCategory: one(categories, {
    fields: [validationQueue.suggestedCategoryId],
    references: [categories.id],
  }),
  suggestedSubtype: one(subtypes, {
    fields: [validationQueue.suggestedSubtypeId],
    references: [subtypes.id],
  }),
}));
