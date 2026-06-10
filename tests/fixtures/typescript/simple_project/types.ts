export interface User {
  id: string;
  name: string;
  greet(prefix: string): Promise<string>;
}

export type UserId = string;

export enum Role {
  Admin,
  Member,
}
