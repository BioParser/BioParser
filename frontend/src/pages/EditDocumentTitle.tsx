import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { type JobList, type JobStatusFullResponse, updateDocumentTitle } from "../api/client";

export function EditDocumentTitle({ job }: { job: JobStatusFullResponse }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const editButton = useRef<HTMLButtonElement>(null);
  const opened = useRef(false);
  useEffect(() => {
    if (editing) input.current?.focus();
    else if (opened.current) editButton.current?.focus();
  }, [editing]);

  const saving = useMutation({
    mutationFn: ({ checksum, title }: { checksum: string; title: string | null }) =>
      updateDocumentTitle(checksum, title),
    onSuccess: async (document) => {
      // A poll started before the save must not put the old title back in the cache.
      await Promise.all([
        queryClient.cancelQueries({ queryKey: ["jobs"] }),
        queryClient.cancelQueries({
          queryKey: ["job"],
          predicate: (query) =>
            (query.state.data as JobStatusFullResponse | undefined)?.document_checksum ===
            document.checksum,
        }),
      ]);
      const update = (stored: JobStatusFullResponse) =>
        stored.document_checksum === document.checksum
          ? { ...stored, title: document.title ?? null }
          : stored;
      queryClient.setQueriesData<JobList>({ queryKey: ["jobs"] }, (jobs) => jobs?.map(update));
      queryClient.setQueriesData<JobStatusFullResponse>({ queryKey: ["job"] }, (stored) =>
        stored ? update(stored) : stored,
      );
      // Refetch on returning to a dashboard, while showing the saved title immediately.
      void queryClient.invalidateQueries({ queryKey: ["jobs"], refetchType: "none" });
    },
  });

  function cancel() {
    if (saving.isPending) return;
    saving.reset();
    setEditing(false);
  }

  return (
    <div className="document-title-editor">
      <p id="document-title-help">
        The title applies to every job of this PDF. Leave it blank to use the filename.
      </p>
      {editing ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (saving.isPending) return;
            saving.mutate(
              { checksum: job.document_checksum, title: title.trim() ? title : null },
              { onSuccess: () => setEditing(false) },
            );
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              cancel();
            }
          }}
        >
          <label className="job-field">
            Title
            <input
              ref={input}
              type="text"
              value={title}
              maxLength={500}
              disabled={saving.isPending}
              aria-describedby="document-title-help"
              onChange={(event) => setTitle(event.currentTarget.value)}
            />
          </label>
          {saving.isError && <p role="alert">The title could not be saved. Try again.</p>}
          <div className="job-actions">
            <button type="submit" disabled={saving.isPending}>
              {saving.isPending ? "Saving…" : "Save"}
            </button>
            <button type="button" disabled={saving.isPending} onClick={cancel}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <>
          <button
            ref={editButton}
            type="button"
            aria-describedby="document-title-help"
            onClick={() => {
              opened.current = true;
              saving.reset();
              setTitle(job.title ?? "");
              setEditing(true);
            }}
          >
            Edit title
          </button>
          {saving.isSuccess && <p role="status">Title saved.</p>}
        </>
      )}
    </div>
  );
}
