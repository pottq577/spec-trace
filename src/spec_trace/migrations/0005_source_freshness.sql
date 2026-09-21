ALTER TABLE planning_documents
ADD COLUMN source_last_edited_time TEXT;

ALTER TABLE source_pages
ADD COLUMN last_collected_notion_edited_time TEXT;
