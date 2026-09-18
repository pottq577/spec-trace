ALTER TABLE planning_documents
ADD COLUMN notion_data_source_id TEXT;

ALTER TABLE planning_documents
ADD COLUMN menu_parent_notion_page_id TEXT;

CREATE INDEX planning_documents_by_data_source
ON planning_documents(notion_data_source_id);
