import type { NextPage } from 'next'
import Head from 'next/head'
import Image from 'next/image'
import styles from '../styles/Home.module.css'

import {facts} from '../public/beaverfacts.json';

const BeaverFact = () => {

  const randomFact = facts[Math.floor(Math.random() * facts.length)];

  return (
    <p>{randomFact.fact}</p>
  )
}

const Home: NextPage = () => {
  return (
    <div className={styles.container}>
      <Head>
        <title>Beaverfacts.com</title>
        <meta name="description" content="Beaver facts" />
        <link rel="icon" href="/favicon.png" />
      </Head>

      <main className={styles.main}>
        <Image 
          src="/Beaverhugs1.png" 
          alt="Drawing of a beaver hugging a maple leaf"
          width={500}
          height={500}
        />
        <BeaverFact />
      </main>

      <footer className={styles.footer}>
        <a
          href="https://vercel.com?utm_source=create-next-app&utm_medium=default-template&utm_campaign=create-next-app"
          target="_blank"
          rel="noopener noreferrer"
        >
          Powered by{' '}
          <span className={styles.logo}>
            <Image src="/vercel.svg" alt="Vercel Logo" width={72} height={16} />
          </span>
        </a>
      </footer>
    </div>
  )
}

export default Home
