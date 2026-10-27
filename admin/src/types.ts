export interface Identity { username: string }
export interface AdminAccount { id: string; username: string; created_at: string; last_login_at: string | null; password_changed_at: string | null }
export interface AuditEvent { id: string; username: string; action: string; outcome: 'success' | 'failure'; reason: string; ip: string; user_agent: string; created_at: string }
export interface Page<T> { items: T[]; total: number; page: number; page_size: number }
export interface User { id: string; created_at: string; last_login_at: string; book_count: number; entry_count: number }
export interface Book {
  id: string; owner_id: string; name: string; kind: 'personal' | 'shared'; status: 'active' | 'closed';
  created_at: string; entry_count: number; member_count: number
}
export interface Member { user_id: string; role: string; status: string; joined_at: string }
export interface BookDetail extends Book { members: Member[] }
export interface Overview {
  users: number; new_users_today: number; active_users_7d: number; books: number; shared_books: number;
  entries: number; new_entries_today: number; timezone: string; generated_at: string;
  trend: { date: string; users: number; entries: number }[]
}
