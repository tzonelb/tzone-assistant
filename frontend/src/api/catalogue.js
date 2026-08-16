import { apiRequest } from "./client";

function createQueryString(parameters) {
  const searchParameters = new URLSearchParams();

  Object.entries(parameters).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") {
      return;
    }

    searchParameters.set(key, String(value));
  });

  const value = searchParameters.toString();
  return value ? `?${value}` : "";
}

export async function getProductsRequest({
  search = "",
  categoryId = "",
  stock = "",
  status = "",
  limit = 24,
  offset = 0,
} = {}) {
  const query = createQueryString({
    search,
    category_id: categoryId,
    stock,
    status,
    limit,
    offset,
  });

  return apiRequest(`/api/catalogue/products${query}`);
}

export async function getProductRequest(productId) {
  return apiRequest(
    `/api/catalogue/products/${encodeURIComponent(productId)}`,
  );
}

export async function createProductRequest(values) {
  return apiRequest("/api/catalogue/products", {
    method: "POST",
    body: values,
  });
}

export async function updateProductRequest(productId, values) {
  return apiRequest(
    `/api/catalogue/products/${encodeURIComponent(productId)}`,
    { method: "PUT", body: values },
  );
}

export async function deleteProductRequest(productId) {
  return apiRequest(
    `/api/catalogue/products/${encodeURIComponent(productId)}`,
    { method: "DELETE" },
  );
}

export async function getCatalogueOptionsRequest() {
  return apiRequest("/api/catalogue/options");
}

export async function getProductCategoriesRequest() {
  return apiRequest("/api/catalogue/categories");
}

export async function createProductCategoryRequest(values) {
  return apiRequest("/api/catalogue/categories", {
    method: "POST",
    body: values,
  });
}

export async function updateProductCategoryRequest(categoryId, values) {
  return apiRequest(
    `/api/catalogue/categories/${encodeURIComponent(categoryId)}`,
    { method: "PUT", body: values },
  );
}

export async function deleteProductCategoryRequest(categoryId) {
  return apiRequest(
    `/api/catalogue/categories/${encodeURIComponent(categoryId)}`,
    { method: "DELETE" },
  );
}
