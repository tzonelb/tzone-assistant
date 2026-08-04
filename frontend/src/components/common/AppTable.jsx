import {
  ArrowDownwardOutlined,
  ArrowUpwardOutlined,
  ChevronLeftOutlined,
  ChevronRightOutlined,
} from "@mui/icons-material";

import EmptyState from "./EmptyState";
import LoadingState from "./LoadingState";


function resolveCellValue(row, column) {
  if (typeof column.valueGetter === "function") {
    return column.valueGetter(row);
  }

  return row[column.key];
}


export default function AppTable({
  columns,
  rows,
  loading = false,
  emptyTitle = "No records found",
  emptyDescription = "There is currently no information to display.",
  rowKey = "id",
  sortKey = "",
  sortDirection = "asc",
  onSort,
  page = 1,
  pageSize = 10,
  totalRows,
  onPageChange,
  renderMobileCard,
}) {
  // Two pagination contracts:
  // - External/server pagination: the caller passes a `totalRows` count and
  //   hands us just the current page's rows already sliced. We render them as-is.
  // - Internal/client pagination: the caller passes the full `rows` array and no
  //   `totalRows`. AppTable owns pagination and slices to the current page here,
  //   so the footer controls actually page through the data.
  const isExternallyPaginated = typeof totalRows === "number";

  const calculatedTotalRows = isExternallyPaginated
    ? totalRows
    : rows.length;

  const totalPages = Math.max(
    1,
    Math.ceil(calculatedTotalRows / pageSize),
  );

  const visibleRows = isExternallyPaginated
    ? rows
    : rows.slice((page - 1) * pageSize, page * pageSize);

  if (loading) {
    return (
      <LoadingState
        title="Loading records..."
        description="Please wait while the information is retrieved."
      />
    );
  }

  if (!rows.length) {
    return (
      <EmptyState
        title={emptyTitle}
        description={emptyDescription}
      />
    );
  }

  function handleSort(column) {
    if (!column.sortable || !onSort) {
      return;
    }

    onSort(column.key);
  }

  return (
    <div className="tz-table-wrapper">
      <div className="tz-table-desktop">
        <table className="tz-table">
          <thead>
            <tr>
              {columns.map((column) => {
                const activeSort =
                  sortKey === column.key;

                return (
                  <th
                    key={column.key}
                    style={{
                      width: column.width,
                      textAlign:
                        column.align || "left",
                    }}
                  >
                    <button
                      type="button"
                      className={
                        column.sortable
                          ? "tz-table-sort-button"
                          : "tz-table-heading"
                      }
                      disabled={!column.sortable}
                      onClick={() =>
                        handleSort(column)
                      }
                    >
                      <span>{column.label}</span>

                      {activeSort ? (
                        sortDirection === "asc" ? (
                          <ArrowUpwardOutlined fontSize="inherit" />
                        ) : (
                          <ArrowDownwardOutlined fontSize="inherit" />
                        )
                      ) : null}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>

          <tbody>
            {visibleRows.map((row, rowIndex) => (
              <tr
                key={
                  row[rowKey] ??
                  `${rowIndex}-${JSON.stringify(row)}`
                }
              >
                {columns.map((column) => {
                  const value =
                    resolveCellValue(row, column);

                  return (
                    <td
                      key={column.key}
                      style={{
                        textAlign:
                          column.align || "left",
                      }}
                    >
                      {typeof column.render ===
                      "function"
                        ? column.render(
                            value,
                            row,
                            rowIndex,
                          )
                        : value ?? "—"}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="tz-table-mobile">
        {visibleRows.map((row, rowIndex) => (
          <article
            className="tz-mobile-record"
            key={
              row[rowKey] ??
              `${rowIndex}-${JSON.stringify(row)}`
            }
          >
            {renderMobileCard ? (
              renderMobileCard(row, rowIndex)
            ) : (
              <div className="tz-mobile-record-fields">
                {columns.map((column) => {
                  const value =
                    resolveCellValue(row, column);

                  return (
                    <div
                      className="tz-mobile-record-field"
                      key={column.key}
                    >
                      <span>{column.label}</span>

                      <strong>
                        {typeof column.render ===
                        "function"
                          ? column.render(
                              value,
                              row,
                              rowIndex,
                            )
                          : value ?? "—"}
                      </strong>
                    </div>
                  );
                })}
              </div>
            )}
          </article>
        ))}
      </div>

      <footer className="tz-table-footer">
        <span>
          Showing{" "}
          {calculatedTotalRows
            ? (page - 1) * pageSize + 1
            : 0}
          {" – "}
          {Math.min(
            page * pageSize,
            calculatedTotalRows,
          )}{" "}
          of {calculatedTotalRows}
        </span>

        <div className="tz-pagination">
          <button
            type="button"
            aria-label="Previous page"
            disabled={page <= 1}
            onClick={() =>
              onPageChange?.(page - 1)
            }
          >
            <ChevronLeftOutlined fontSize="small" />
          </button>

          <span>
            Page {page} of {totalPages}
          </span>

          <button
            type="button"
            aria-label="Next page"
            disabled={page >= totalPages}
            onClick={() =>
              onPageChange?.(page + 1)
            }
          >
            <ChevronRightOutlined fontSize="small" />
          </button>
        </div>
      </footer>
    </div>
  );
}