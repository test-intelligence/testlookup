import type { AxiosRequestConfig } from 'axios'
import { api } from './api'

type RequestConfig = Omit<AxiosRequestConfig, 'data' | 'method' | 'url'>

export function getData<T>(url: string, config?: RequestConfig): Promise<T> {
  return api.get<T>(url, config).then(({ data }) => data)
}

export function postData<TResponse, TBody = unknown>(
  url: string,
  body?: TBody,
  config?: RequestConfig,
): Promise<TResponse> {
  return api.post<TResponse>(url, body, config).then(({ data }) => data)
}

export function putData<TResponse, TBody = unknown>(
  url: string,
  body?: TBody,
  config?: RequestConfig,
): Promise<TResponse> {
  return api.put<TResponse>(url, body, config).then(({ data }) => data)
}

export function patchData<TResponse, TBody = unknown>(
  url: string,
  body?: TBody,
  config?: RequestConfig,
): Promise<TResponse> {
  return api.patch<TResponse>(url, body, config).then(({ data }) => data)
}

export function deleteData<TResponse = void>(url: string, config?: RequestConfig): Promise<TResponse> {
  return api.delete<TResponse>(url, config).then(({ data }) => data)
}
