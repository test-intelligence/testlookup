import type { AxiosRequestConfig } from 'axios'
import { api } from './api'
import { isScopedFetchActive } from './scopeAbortCore'

type RequestConfig = Omit<AxiosRequestConfig, 'data' | 'method' | 'url'>

export function getData<T>(url: string, config?: RequestConfig): Promise<T> {
  // Inside a scoped hook's SWR fetcher (`scopedFetch`), opt the read into
  // superseded-scope aborting (services/scopeAbort.ts). Anywhere else the
  // config is passed through untouched.
  const effective = isScopedFetchActive() ? { ...config, scopeTracked: true } : config
  return api.get<T>(url, effective).then(({ data }) => data)
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

export function deleteData<TResponse = void, TBody = never>(
  url: string,
  config?: RequestConfig,
  body?: TBody,
): Promise<TResponse> {
  return api.delete<TResponse>(url, {
    ...config,
    ...(body === undefined ? {} : { data: body }),
  }).then(({ data }) => data)
}
